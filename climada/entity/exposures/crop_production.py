"""
This file is part of CLIMADA.

Copyright (C) 2017 ETH Zurich, CLIMADA contributors listed in AUTHORS.

CLIMADA is free software: you can redistribute it and/or modify it under the
terms of the GNU Lesser General Public License as published by the Free
Software Foundation, version 3.

CLIMADA is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE.  See the GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License along
with CLIMADA. If not, see <https://www.gnu.org/licenses/>.
"""


import logging
import os
from os import listdir
from os.path import isfile, isdir, join
import math
import copy
import numpy as np
import xarray as xr
import pandas as pd
import h5py
from matplotlib import pyplot as plt
from iso3166 import countries as iso_cntry
from climada.entity.exposures.base import Exposures
from climada.entity.tag import Tag
import climada.util.coordinates as coord
from climada.util.constants import DATA_DIR, DEF_CRS
from climada.util.coordinates import pts_to_raster_meta, get_resolution


logging.root.setLevel(logging.DEBUG)
LOGGER = logging.getLogger(__name__)

DEF_HAZ_TYPE = 'RC'
"""Default hazard type used in impact functions id."""

BBOX = [-180, -85, 180, 85]  # [Lon min, lat min, lon max, lat max]
""""Default geographical bounding box of the total global agricultural land extent"""

#ISIMIP input data specific global variables
YEARCHUNKS = dict()
"""start and end years per ISIMIP version and senario as in ISIMIP-filenames
of landuse data containing harvest area per crop"""
# two types of 1860soc (1661-2299 not implemented)
YEARCHUNKS['ISIMIP2'] = dict()
YEARCHUNKS['ISIMIP2']['1860soc'] = {'yearrange': (1800, 1860), 'startyear': 1661, 'endyear': 1860}
YEARCHUNKS['ISIMIP2']['histsoc'] = {'yearrange': (1976, 2005), 'startyear': 1861, 'endyear': 2005}
YEARCHUNKS['ISIMIP2']['2005soc'] = {'yearrange': (2006, 2099), 'startyear': 2006, 'endyear': 2299}
YEARCHUNKS['ISIMIP2']['rcp26soc'] = {'yearrange': (2006, 2099), 'startyear': 2006, 'endyear': 2099}
YEARCHUNKS['ISIMIP2']['rcp60soc'] = {'yearrange': (2006, 2099), 'startyear': 2006, 'endyear': 2099}
YEARCHUNKS['ISIMIP2']['2100rcp26soc'] = {'yearrange': (2100, 2299), 'startyear': 2100,
                              'endyear': 2299}
YEARCHUNKS['ISIMIP3'] = dict()
YEARCHUNKS['ISIMIP3']['histsoc'] = {'yearrange': (1983, 2013), 'startyear': 1850, 'endyear': 2014}
YEARCHUNKS['ISIMIP3']['2015soc'] = {'yearrange': (1983, 2013), 'startyear': 1850, 'endyear': 2014}

FN_STR_VAR = 'landuse-15crops_annual'
"""fix filename part in input data"""

CROP_NAME = dict()
"""mapping of crop names"""
CROP_NAME['mai'] = {'input': 'maize', 'fao': 'Maize', 'print': 'Maize'}
CROP_NAME['ric'] = {'input': 'rice', 'fao': 'Rice, paddy', 'print': 'Rice'}
CROP_NAME['whe'] = {'input': 'temperate_cereals', 'fao': 'Wheat', 'print': 'Wheat'}
CROP_NAME['soy'] = {'input': 'oil_crops_soybean', 'fao': 'Soybeans', 'print': 'Soybeans'}

CROP_NAME['ri1'] = {'input': 'rice', 'fao': 'Rice, paddy', 'print': 'Rice 1st season'}
CROP_NAME['ri2'] = {'input': 'rice', 'fao': 'Rice, paddy', 'print': 'Rice 2nd season'}
CROP_NAME['swh'] = {'input': 'temperate_cereals', 'fao': 'Wheat', 'print': 'Spring Wheat'}
CROP_NAME['wwh'] = {'input': 'temperate_cereals', 'fao': 'Wheat', 'print': 'Winter Wheat'}

"""mapping of irrigation parameter long names"""
IRR_NAME = {'combined': {'name': 'combined'},
            'noirr': {'name': 'rainfed'},
            'firr': {'name': 'irrigated'},
            }

"""Conversion factor weight to kcal.
    Sources:
        - Nuss and Tanumihardjo, 2010. https://doi.org/10.1111/j.1541-4337.2010.00117.x
        - USDA Nutrient Database"""
KCAL_PER_TON = {'mai': 365e4,
                'ric': 360e4,
                'whe': 339e4,
                'soy': 446e4,
                }

# default:
#   deposit the landuse files in the directory: climada_python/data/ISIMIP_crop/Input/Exposure
#   deposit the FAO files in the directory: climada_python/data/ISIMIP_crop/Input/Exposure/FAO
# The FAO files need to be downloaded and renamed
#   FAO_FILE: contains producer prices per crop, country and year
#               (http://www.fao.org/faostat/en/#data/PP)
#   FAO_FILE2: contains production quantity per crop, country and year
#               (http://www.fao.org/faostat/en/#data/QC)
INPUT_DIR = os.path.join(DATA_DIR, 'ISIMIP_crop', 'Input', 'Exposure')
FAO_FILE = "FAOSTAT_data_producer_prices.csv"
FAO_FILE2 = "FAOSTAT_data_production_quantity.csv"

YEARS_FAO = (2000, 2018)
"""Default years from FAO used (data file contains values for 1991-2018)"""

# default output directory: climada_python/data/ISIMIP_crop/Output/Exposure
# by default the hist_mean files created by climada_python/hazard/crop_potential are saved in
# climada_python/data/ISIMIP_crop/Output/hist_mean/
HIST_MEAN_PATH = os.path.join(DATA_DIR, 'ISIMIP_crop', 'Output', 'Hist_mean')
OUTPUT_DIR = os.path.join(DATA_DIR, 'ISIMIP_crop', 'Output')


class CropProduction(Exposures):
    """Defines agriculture exposures from ISIMIP input data and FAO crop data

    geopandas GeoDataFrame with metadata and columns (pd.Series) defined in
    Attributes and Exposures.

    Attributes:
        crop (str): crop type f.i. 'mai', 'ric', 'whe', 'soy'

    """

    _metadata = Exposures._metadata + ['crop']

    @property
    def _constructor(self):
        return CropProduction

    def set_from_isimip_netcdf(self, input_dir=None, filename=None, hist_mean=None,
                            bbox=BBOX, yearrange=None, cl_model=None, scenario=None,
                            soc=None, crop=None, irr=None, isimip_version=None,
                            unit=None, fn_str_var=None):

        """Wrapper to fill exposure from NetCDF file from ISIMIP. Requires historical
        mean relative cropyield module as additional input.
        Parameters:
            input_dir (string): path to input data directory
            filename (string): name of the landuse data file to use,
                e.g. "histsoc_landuse-15crops_annual_1861_2005.nc""
            hist_mean (str or array): historic mean crop yield per centroid (or path)
            bbox (list of four floats): bounding box:
                [lon min, lat min, lon max, lat max]
            yearrange (int tuple): year range for exposure set
                f.i. (1990, 2010)
            scenario (string): climate change and socio economic scenario
                f.i. '1860soc', 'histsoc', '2005soc', 'rcp26soc','rcp60soc','2100rcp26soc'
            cl_model (string): abbrev. climate model (only for future projections of lu data)
                f.i. 'gfdl-esm2m', 'hadgem2-es', 'ipsl-cm5a-lr','miroc5'
            crop (string): crop type
                f.i. 'mai', 'ric', 'whe', 'soy'
            irr (string): irrigation type
                f.i 'firr' (full irrigation), 'noirr' (no irrigation) or 'combined'= firr+noirr
            isimip_version(str): 'ISIMIP2' (default) or 'ISIMIP3'
            unit (string): unit of the exposure (per year)
                f.i 'USD' or 't' (default) or 'kcal'
            fn_str_var (string): FileName STRing depending on VARiable and
                ISIMIP simuation round

        Returns:
            Exposure
        """
        if not input_dir: input_dir = INPUT_DIR
        if hist_mean is None: hist_mean = HIST_MEAN_PATH
        if not fn_str_var: fn_str_var = FN_STR_VAR
        if (not isimip_version) or ('ISIMIP2' in isimip_version):
            isimip_version = 'ISIMIP2'
        elif 'ISIMIP3' in isimip_version:
            isimip_version = 'ISIMIP3'
        if (not scenario) or ('hist' in scenario): scenario = 'histsoc'
        if yearrange is None: yearrange = YEARCHUNKS[isimip_version][scenario]['yearrange']
        if not unit: unit = 't'
        #if not soc: soc=''
        # The filename is set or other variables (cl_model, scenario) are extracted of the
        # specified filename
        if filename is None:
            yearchunk = YEARCHUNKS[isimip_version][scenario]
            # if scenario == 'histsoc' or scenario == '1860soc':
            if scenario in ('histsoc', '1860soc'):
                string = '%s_%s_%s_%s.nc'
                filename = os.path.join(input_dir, string % (scenario, fn_str_var,
                                                             str(yearchunk['startyear']),
                                                             str(yearchunk['endyear'])))
            else:
                string = '%s_%s_%s_%s_%s.nc'
                filename = os.path.join(input_dir, string % (scenario, cl_model, fn_str_var,
                                                             str(yearchunk['startyear']),
                                                             str(yearchunk['endyear'])))
        elif scenario == 'flexible':
            _, _, _, _, _, _, startyear, endyearnc = filename.split('_')
            endyear = endyearnc.split('.')[0]
            yearchunk = dict()
            yearchunk = {'yearrange': (int(startyear), int(endyear)),
                         'startyear': int(startyear), 'endyear': int(endyear)}
            filename = os.path.join(input_dir, filename)
        else:
            scenario, *_ = filename.split('_')
            yearchunk = YEARCHUNKS[isimip_version][scenario]
            filename = os.path.join(input_dir, filename)

        # Dataset is opened and data within the bbox extends is extracted
        data_set = xr.open_dataset(filename, decode_times=False)
        [lonmin, latmin, lonmax, latmax] = bbox
        data = data_set.sel(lon=slice(lonmin, lonmax), lat=slice(latmax, latmin))

        # The latitude and longitude are set; the region_id is determined
        lon, lat = np.meshgrid(data.lon.values, data.lat.values)
        self['latitude'] = lat.flatten()
        self['longitude'] = lon.flatten()
        self['region_id'] = coord.get_country_code(self.latitude, self.longitude)

        # The indeces of the yearrange to be extracted are determined
        time_idx = (int(yearrange[0] - yearchunk['startyear']),
                             int(yearrange[1] - yearchunk['startyear']))

        # The area covered by a grid cell is calculated depending on the latitude
        # 1 degree = 111.12km (at the equator); resolution data: 0.5 degree;
        # longitudal distance in km = 111.12*0.5*cos(lat);
        # latitudal distance in km = 111.12*0.5;
        # area = longitudal distance * latitudal distance;
        # 1km2 = 100ha
        area = (111.12 * 0.5)**2 * np.cos(np.deg2rad(lat)) * 100

        # The area covered by a crop is calculated as the product of the fraction and
        # the grid cell size
        if irr == 'combined':
            irr = ['firr', 'noirr']
        else:
            irr = [irr]
        area_crop = dict()
        for irr_var in irr:
            area_crop[irr_var] = (
                getattr(
                    data, (CROP_NAME[crop])['input']+'_'+ (IRR_NAME[irr_var])['name']
                )[time_idx[0]:time_idx[1], :, :].mean(dim='time')*area
            ).values
            area_crop[irr_var] = np.nan_to_num(area_crop[irr_var]).flatten()

        # set historic mean, its latitude, and longitude:
        hist_mean_dict = dict()
        # if hist_mean is given as np.ndarray or dict, 
        # code assumes it contains hist_mean as returned by the hazard crop_potential
        # however structured in dictionary as hist_mean_dict, with same
        # bbox extensions as the exposure:
        if isinstance(hist_mean, dict):
            if not ('firr' in hist_mean.keys() or 'noirr' in hist_mean.keys()):
                LOGGER.error('Invalid hist_mean provided: {hist_mean}')
                raise ValueError('invalid hist_mean.')
            hist_mean_dict = hist_mean
            lat_mean = self.latitude.values
        elif isinstance(hist_mean, np.ndarray):
            hist_mean_dict[irr[0]] = hist_mean
            lat_mean = self.latitude.values
        elif isdir(hist_mean): # else if hist_mean is given as path to directory
        # The adequate file from the directory (depending on crop and irrigation) is extracted
        # and the variables hist_mean, lat_mean and lon_mean are set accordingly
            for irr_var in irr:
                filename = os.path.join(hist_mean, 'hist_mean_%s-%s_%i-%i.hdf5' %(\
                                        crop, irr_var, yearrange[0], yearrange[1])
                                        )
                hist_mean_dict[irr_var] = (h5py.File(filename, 'r'))['mean'][()]
            lat_mean = (h5py.File(filename, 'r'))['lat'][()]
            lon_mean = (h5py.File(filename, 'r'))['lon'][()]
        elif isfile(os.path.join(input_dir, hist_mean)): # file path
        # Hist_mean, lat_mean and lon_mean are extracted from the given file
            if len(irr) > 1:
                LOGGER.error('For irr=combined, hist_mean can not be single file. Aborting.')
                raise ValueError('Wrong combination of parameters irr and hist_mean.')
            hist_mean = h5py.File(os.path.join(input_dir, hist_mean), 'r')
            hist_mean_dict[irr[0]] = hist_mean['mean'][()]
            lat_mean = hist_mean['lat'][()]
            lon_mean = hist_mean['lon'][()]
        else:
            LOGGER.error('Invalid hist_mean provided: {hist_mean}')
            raise ValueError('invalid hist_mean.')

        # The bbox is cut out of the hist_mean data file if needed
        if len(lat_mean) != len(self.latitude.values):
            idx_mean = np.zeros(len(self.latitude.values), dtype=int)
            for i in range(len(self.latitude.values)):
                idx_mean[i] = np.where(
                    (lat_mean == self.latitude.values[i])
                    & (lon_mean == self.longitude.values[i])
                )[0][0]
        else:
            idx_mean = np.arange(0, len(lat_mean))

        # The exposure [t/y] is computed per grid cell as the product of the area covered
        # by a crop [ha] and its yield [t/ha/y]
        self['value'] = np.squeeze(area_crop[irr[0]]*hist_mean_dict[irr[0]][idx_mean])
        self['value'] = np.nan_to_num(self.value) # replace NaN by 0.0
        for irr_val in irr[1:]: # add other irrigation types if irr=combined
            value_tmp = np.squeeze(area_crop[irr_val]*hist_mean_dict[irr_val][idx_mean])
            value_tmp = np.nan_to_num(value_tmp) # replace NaN by 0.0
            self['value'] += value_tmp
        self.tag = Tag()
        if len(irr) > 1:
            irr = 'combined'
        else:
            irr = irr[0]
        self.tag.description = ("Crop production exposure from ISIMIP " +
                                (CROP_NAME[crop])['print'] + ' ' +
                                irr + ' ' + str(yearrange[0]) + '-' + str(yearrange[-1]))
        self.value_unit = 't / y'
        self.crop = crop
        self.ref_year = yearrange
        self.crs = DEF_CRS
        try:
            rows, cols, ras_trans = pts_to_raster_meta(
                (self.longitude.min(), self.latitude.min(),
                 self.longitude.max(), self.latitude.max()),
                get_resolution(self.longitude, self.latitude))
            self.meta = {
                'width': cols,
                'height': rows,
                'crs': self.crs,
                'transform': ras_trans,
            }
        except ValueError:
            LOGGER.warning('Could not write attribute meta, because exposure'
                           ' has only 1 data point')
            self.meta = {}

        if 'USD' in unit:
            # set_to_usd() is called to compute the exposure in USD/y (country specific)
            self.set_to_usd(input_dir=input_dir)
        elif 'kcal' in unit:
            # set_to_kcal() is called to compute the exposure in kcal/y
            self.set_to_kcal()
        self.check()

        return self

    def set_mean_of_several_models(self, input_dir=None, hist_mean=None, bbox=BBOX,
                                   yearrange=None, cl_model=None, scenario=None,
                                   crop=None, irr=None, isimip_version=None,
                                   unit=None, fn_str_var=None):
        """Wrapper to fill exposure from several NetCDF files with crop yield data
        from ISIMIP.

        Optional Parameters:
            input_dir (string): path to input data directory
            historic mean (array): historic mean crop production per centroid
            bbox (list of four floats): bounding box:
                [lon min, lat min, lon max, lat max]
            yearrange (int tuple): year range for exposure set, f.i. (1976, 2005)
            scenario (string): climate change and socio economic scenario
                f.i. 'histsoc' or 'rcp60soc'
            cl_model (string): abbrev. climate model (only when landuse data
            is future projection)
                f.i. 'gfdl-esm2m' etc.
            crop (string): crop type
                f.i. 'mai', 'ric', 'whe', 'soy'
            irr (string): irrigation type
                f.i 'rainfed', 'irrigated' or 'combined'= rainfed+irrigated
            isimip_version(str): 'ISIMIP2' (default) or 'ISIMIP3'
            unit (string): unit of the exposure (per year)
                f.i 'USD' or 't' (default)
            fn_str_var (string): FileName STRing depending on VARiable and
                ISIMIP simuation round
        Returns:
            Exposure
        """
        if (not isimip_version) or ('ISIMIP2' in isimip_version):
            isimip_version = 'ISIMIP2'
        elif 'ISIMIP3' in isimip_version:
            isimip_version = 'ISIMIP3'
        if not input_dir: input_dir = INPUT_DIR
        if not hist_mean: hist_mean = HIST_MEAN_PATH
        if yearrange is None: yearrange = YEARCHUNKS[isimip_version]['histsoc']['yearrange']
        if not unit: unit = 't'
        if not fn_str_var: fn_str_var = FN_STR_VAR
        filenames = dict()
        filenames['all'] = [f for f in listdir(input_dir) if (isfile(join(input_dir, f)))
                            if not f.startswith('.') if 'nc' in f]

        # If only files with a certain scenario and or cl_model shall be considered, they
        # are extracted from the original list of files
        filenames['subset'] = list()
        for name in filenames['all']:
            if cl_model is not None and scenario is not None:
                if cl_model in name or scenario in name:
                    filenames['subset'].append(name)
            elif cl_model is not None and scenario is None:
                if cl_model in name:
                    filenames['subset'].append(name)
            elif cl_model is None and scenario is not None:
                if scenario in name:
                    filenames['subset'].append(name)
            else:
                filenames['subset'] = filenames['all']

        # The first exposure is calculate to determine its size
        # and initialize the combined exposure
        self.set_from_isimip_netcdf(input_dir, filename=filenames['subset'][0],
                                 hist_mean=hist_mean, bbox=bbox, yearrange=yearrange,
                                 crop=crop, irr=irr, isimip_version=isimip_version,
                                 unit=unit, fn_str_var=fn_str_var)

        combined_exp = np.zeros([self.value.size, len(filenames['subset'])])
        combined_exp[:, 0] = self.value

        # The calculations are repeated for all remaining exposures (starting from index 1 as
        # the first exposure has been saved in combined_exp[:, 0])
        for j in range(1, len(filenames['subset'])):
            self.set_from_isimip_netcdf(input_dir, filename=filenames['subset'][j],
                                     hist_mean=hist_mean, bbox=bbox, yearrange=yearrange,
                                     crop=crop, irr=irr, unit=unit, isimip_version=isimip_version)
            combined_exp[:, j] = self.value

        self['value'] = np.mean(combined_exp, 1)
        self['crop'] = crop

        self.check()

        return self

    def set_to_kcal(self):
        """Converts the exposure from tonnes to kcal using conversion factor per crop type.

        Returns:
            Exposure
        """
        if not 't' in self.value_unit:
            LOGGER.warning('self.unit is neither t nor t / year.')
        self['tonnes_per_year'] = self['value'].values
        self.value = self.value * KCAL_PER_TON[self.crop]
        self.value_unit = 'kcal / y'
        return self

    def set_to_usd(self, input_dir=None, yearrange=None):
        # to do: check api availability?; default yearrange for single year (e.g. 5a)
        """Calculates the exposure in USD using country and year specific data published
        by the FAO.

        Optional Parameters:
            input_dir (string): directory containing the input (FAO pricing) data
            yearrange (array): year range for prices, can also be set to a single year
                Default is set to the arbitrary time range (2000, 2018)
                The data is available for the years 1991-2018
            crop (str): crop type
                f.i. 'mai', 'ric', 'whe', 'soy'

        Returns:
            Exposure
        """
        if not input_dir: input_dir = INPUT_DIR
        if yearrange is None: yearrange = YEARS_FAO
        # the exposure in t/y is saved as 'tonnes_per_year'
        self['tonnes_per_year'] = self['value'].values

        # account for the case of only specifying one year as yearrange
        if len(yearrange) == 1:
            yearrange = (yearrange[0], yearrange[0])

        # open both FAO files and extract needed variables
        # FAO_FILE: contains producer prices per crop, country and year
        fao = dict()
        fao['file'] = pd.read_csv(os.path.join(input_dir, FAO_FILE))
        fao['crops'] = fao['file'].Item.values
        fao['year'] = fao['file'].Year.values
        fao['price'] = fao['file'].Value.values

        fao_country = coord.country_faocode2iso(getattr(fao['file'], 'Area Code').values)

        # create a list of the countries contained in the exposure
        iso3alpha = list()
        for reg_id in self.region_id:
            try:
                iso3alpha.append(iso_cntry.get(reg_id).alpha3)
            except KeyError:
                if reg_id in (0, -99):
                    iso3alpha.append('No country')
                else:
                    iso3alpha.append('Other country')
        list_countries = np.unique(iso3alpha)

        # iterate over all countries that are covered in the exposure, extract the according price
        # and calculate the crop production in USD/y
        area_price = np.zeros(self.value.size)
        for country in list_countries:
            [idx_country] = np.where(np.asarray(iso3alpha) == country)
            if country == 'Other country':
                price = 0
                area_price[idx_country] = self.value[idx_country] * price
            elif country != 'No country' and country != 'Other country':
                idx_price = np.where((np.asarray(fao_country) == country) &
                                     (np.asarray(fao['crops']) == \
                                     (CROP_NAME[self.crop])['fao']) &
                                     (fao['year'] >= yearrange[0]) &
                                     (fao['year'] <= yearrange[1]))
                price = np.mean(fao['price'][idx_price])
                # if no price can be determined for a specific yearrange and country, the world
                # average for that crop (in the specified yearrange) is used
                if math.isnan(price) or price == 0:
                    idx_price = np.where((np.asarray(fao['crops']) == \
                                          (CROP_NAME[self.crop])['fao']) &
                                         (fao['year'] >= yearrange[0]) &
                                         (fao['year'] <= yearrange[1]))
                    price = np.mean(fao['price'][idx_price])
                area_price[idx_country] = self.value[idx_country] * price


        self['value'] = area_price
        self.value_unit = 'USD / y'
        self.check()
        return self

    def aggregate_countries(self):
        """Aggregate exposure data by country.

        Returns:
            list_countries (list): country codes (numerical ISO3)
            country_values (array): aggregated exposure value
        """

        list_countries = np.unique(self.region_id)
        country_values = np.zeros(len(list_countries))
        for i, iso_nr in enumerate(list_countries):
            country_values[i] = self.loc[self.region_id == iso_nr].value.sum()

        return list_countries, country_values

def init_full_exp_set_isimip(input_dir=None, filename=None, hist_mean_dir=None,
                           output_dir=None, bbox=BBOX, yearrange=None, unit=None,
                           isimip_version=None, return_data=False):
    """Generates CropProduction instances (exposure sets) for all files found in the
        input directory and saves them as hdf5 files in the output directory.
        Exposures are aggregated per crop and irrigation type.

        Parameters:
        input_dir (string): path to input data directory
        filename (string): if not specified differently, the file
            'histsoc_landuse-15crops_annual_1861_2005.nc' will be used
        output_dir (string): path to output data directory
        bbox (list of four floats): bounding box:
            [lon min, lat min, lon max, lat max]
        yearrange (array): year range for hazard set, f.i. (1976, 2005)
        isimip_version(str): 'ISIMIP2' (default) or 'ISIMIP3'
        unit (str): unit in which to return exposure (t/y or USD/y)
        return_data (boolean): returned output
            False: returns list of filenames only, True: returns also list of data

    Returns:
        filename_list (list): all filenames of saved initiated exposure files
        output_list (list): list containing all inisiated Exposure instances
    """
    if (not isimip_version) or ('ISIMIP2' in isimip_version):
        isimip_version = 'ISIMIP2'
    elif 'ISIMIP3' in isimip_version:
        isimip_version = 'ISIMIP3'
    if not input_dir: input_dir = INPUT_DIR
    if not hist_mean_dir: hist_mean_dir =HIST_MEAN_PATH
    if not output_dir: output_dir = OUTPUT_DIR
    if yearrange is None: yearrange = YEARCHUNKS[isimip_version]['histsoc']['yearrange']
    if not unit: unit = 't'

    filenames = [f for f in listdir(hist_mean_dir) if (isfile(join(hist_mean_dir, f))) if not
                 f.startswith('.')]

    # generate output directory if it does not exist yet
    if not os.path.exists(os.path.join(output_dir, 'Exposure')):
        os.mkdir(os.path.join(output_dir, 'Exposure'))

    # create exposures for all crop-irrigation combinations and save them
    filename_list = list()
    output_list = list()
    for file in filenames:
        _, _, crop_irr, *_ = file.split('_')
        crop, irr = crop_irr.split('-')
        crop_production = CropProduction()
        crop_production.set_from_isimip_netcdf(input_dir=input_dir, filename=filename,
                                            hist_mean=hist_mean_dir, bbox=bbox,
                                            isimip_version=isimip_version,
                                            yearrange=yearrange, crop=crop, irr=irr, unit=unit)
        filename_expo = ('crop_production_' + crop + '-'+ irr + '_'
                         + str(yearrange[0]) + '-' + str(yearrange[1]) + '.hdf5')
        filename_list.append(filename_expo)
        crop_production.write_hdf5(os.path.join(output_dir, 'Exposure', filename_expo))
        if return_data: output_list.append(crop_production)

    return filename_list, output_list

def normalize_with_fao_cp(exp_firr, exp_noirr, input_dir=None,
                          yearrange=None, unit=None, return_data=True):
    """Normalize (i.e., bias corrent) the given exposures countrywise with the mean
    crop production quantity documented by the FAO.
    Refer to the beginning of the script for guidance on where to download the
    required cropmporduction data from FAO.Stat.

    Parameters:
        exp_firr (crop_production): exposure under full irrigation
        exp_noirr (crop_production): exposure under no irrigation

    Optional Parameters:
        input_dir (str): directory containing exposure input data
        yearrange (array): the mean crop production in this year range is used to normalize
            the exposure data
            Default is set to the arbitrary time range (2008, 2018)
            The data is available for the years 1961-2018
        unit (str): unit in which to return exposure (t/y or USD/y)
        return_data (boolean): returned output
            True: returns country list, ratio = FAO/ISIMIP, normalized exposures, crop production
            per country as documented by the FAO and calculated by the ISIMIP dataset
            False: country list, ratio = FAO/ISIMIP, normalized exposures

    Returns:
        country_list (list): List of country codes (numerical ISO3)
        ratio (list): List of ratio of FAO crop production and aggregated exposure
            for each country
        exp_firr_norm (CropProduction): Normalized CropProduction (full irrigation)
        exp_noirr_norm (CropProduction): Normalized CropProduction (no irrigation)

    Returns (optional):
        fao_crop_production (list): FAO crop production value per country
        exp_tot_production(list): Exposure crop production value per country
            (before normalization)
    """
    if not input_dir: input_dir = INPUT_DIR
    if yearrange is None: yearrange = (2008, 2018)
    if not unit: unit = 't'
    # if the exposure unit is USD/y or kcal/y, temporarily reset the exposure to t/y
    # (stored in tonnes_per_year) in order to normalize with FAO crop production
    # values and then apply set_to_XXX() for the normalized exposure to restore the
    # initial exposure unit
    if exp_firr.value_unit == 'USD / y' or 'kcal' in exp_firr.value_unit:
        exp_firr.value = exp_firr.tonnes_per_year
    if exp_noirr.value_unit == 'USD / y' or 'kcal' in exp_noirr.value_unit:
        exp_noirr.value = exp_noirr.tonnes_per_year

    country_list, countries_firr = exp_firr.aggregate_countries()
    country_list, countries_noirr = exp_noirr.aggregate_countries()

    exp_tot_production = countries_firr + countries_noirr

    fao = pd.read_csv(os.path.join(input_dir, FAO_FILE2))
    fao_crops = fao.Item.values
    fao_year = fao.Year.values
    fao_values = fao.Value.values
    fao_code = getattr(fao, 'Area Code').values

    fao_country = coord.country_iso2faocode(country_list)

    fao_crop_production = np.zeros(len(country_list))
    ratio = np.ones(len(country_list))
    exp_firr_norm = copy.deepcopy(exp_firr)
    exp_noirr_norm = copy.deepcopy(exp_noirr)

    # loop over countries: compute ratio & apply normalization:
    for country, iso_nr in enumerate(country_list):
        idx = np.where((np.asarray(fao_code) == fao_country[country])
                       & (np.asarray(fao_crops) == (CROP_NAME[exp_firr.crop])['fao'])
                       & (fao_year >= yearrange[0]) & (fao_year <= yearrange[1]))
        if len(idx) >= 1:
            fao_crop_production[country] = np.mean(fao_values[idx])

        # if a country has no values in the exposure (e.g. Cyprus) the exposure value
        # is set to the FAO average value
        # in this case the ratio is left being 1 (as initiated)
        if exp_tot_production[country] == 0:
            exp_tot_production[country] = fao_crop_production[country]
        elif fao_crop_production[country] != np.nan and fao_crop_production[country] != 0:
            ratio[country] = fao_crop_production[country] / exp_tot_production[country]

        exp_firr_norm.value[exp_firr.region_id == iso_nr] = ratio[country] * \
        exp_firr.value[exp_firr.region_id == iso_nr]
        exp_noirr_norm.value[exp_firr.region_id == iso_nr] = ratio[country] * \
        exp_noirr.value[exp_noirr.region_id == iso_nr]

        if unit == 'USD' or exp_noirr.value_unit == 'USD / y':
            exp_noirr.set_to_usd(input_dir=input_dir)
        elif 'kcal' in unit or 'kcal' in exp_noirr.value_unit:
            exp_noirr.set_to_kcal()
        if unit == 'USD' or exp_firr.value_unit == 'USD / y':
            exp_firr.set_to_usd(input_dir=input_dir)
        elif 'kcal' in unit or 'kcal' in exp_firr.value_unit:
            exp_firr.set_to_kcal()

    exp_firr_norm.tag.description = exp_firr_norm.tag.description+' normalized'
    exp_noirr_norm.tag.description = exp_noirr_norm.tag.description+' normalized'

    if return_data:
        return country_list, ratio, exp_firr_norm, exp_noirr_norm, \
            fao_crop_production, exp_tot_production
    return country_list, ratio, exp_firr_norm, exp_noirr_norm

def normalize_several_exp(input_dir=None, output_dir=None,
                          yearrange=None, unit=None, return_data=True):
    """
    Multiple exposure sets saved as HDF5 files in input directory are normalized
    (i.e. bias corrected) against FAO statistics of crop production.
        Optional Parameters:
            input_dir (str): directory containing exposure input data
            output_dir (str): directory containing exposure datasets (output of exposure creation)
            yearrange (array): the mean crop production in this year range is used to normalize
                the exposure data (default 2008-2018)
            unit (str): unit in which to return exposure (t/y or USD/y)
            return_data (boolean): returned output
                True: lists containing data for each exposure file. Lists: crops, country list,
                    ratio = FAO/ISIMIP, normalized exposures, crop production per country as documented
                    by the FAO and calculated by the ISIMIP dataset
                False: lists containing data for each exposure file. Lists: crops, country list,
                    ratio = FAO/ISIMIP, normalized exposures

        Returns:
            crop_list (list): List of crops
            country_list (list): List of country codes (numerical ISO3)
            ratio (list): List of ratio of FAO crop production and aggregated exposure
                for each country
            exp_firr_norm (list): List of normalized CropProduction Exposures (full irrigation)
            exp_noirr_norm (list): List of normalize CropProduction Exposures (no irrigation)

        Returns (optional):
            fao_crop_production (list): FAO crop production value per country
            exp_tot_production(list): Exposure crop production value per country
                (before normalization)
    """
    if not input_dir: input_dir = INPUT_DIR
    if not output_dir: output_dir = OUTPUT_DIR
    if not unit: unit = 't'
    if yearrange is None: yearrange = (2008, 2018)
    filenames_firr = [f for f in listdir(os.path.join(output_dir, 'Exposure')) if
                      (isfile(join(os.path.join(output_dir, 'Exposure'), f))) if not
                      f.startswith('.') if 'firr' in f]

    crop_list = list()
    countries_list = list()
    ratio_list = list()
    exp_firr_norm = list()
    exp_noirr_norm = list()
    fao_cp_list = list()
    exp_tot_cp_list = list()

    for file_firr in filenames_firr:
        _, _, crop_irr, years = file_firr.split('_')
        crop, _ = crop_irr.split('-')
        exp_firr = CropProduction()
        exp_firr.read_hdf5(os.path.join(output_dir, 'Exposure', file_firr))

        filename_noirr = 'crop_production_' + crop + '-' + 'noirr' + '_' + years
        exp_noirr = CropProduction()
        exp_noirr.read_hdf5(os.path.join(output_dir, 'Exposure', filename_noirr))

        if return_data:
            countries, ratio, exp_firr2, exp_noirr2, fao_cp, \
            exp_tot_cp = normalize_with_fao_cp(exp_firr, exp_noirr, input_dir=input_dir,
                                               yearrange=yearrange, unit=unit)
            fao_cp_list.append(fao_cp)
            exp_tot_cp_list.append(exp_tot_cp)
        else:
            countries, ratio, exp_firr2, \
            exp_noirr2 = normalize_with_fao_cp(exp_firr, exp_noirr, input_dir=input_dir,
                                               yearrange=yearrange, unit=unit,
                                               return_data=False)

        crop_list.append(crop)
        countries_list.append(countries)
        ratio_list.append(ratio)
        exp_firr_norm.append(exp_firr2)
        exp_noirr_norm.append(exp_noirr2)

    if return_data:
        return crop_list, countries_list, ratio_list, exp_firr_norm, exp_noirr_norm, \
                fao_cp_list, exp_tot_cp_list
    return crop_list, countries_list, ratio_list, exp_firr_norm, exp_noirr_norm

def semilogplot_ratio(crop, countries, ratio, output_dir=OUTPUT_DIR, save=True):
    """Plot ratio = FAO/ISIMIP against country codes.

        Parameters:
            crop (str): crop to plot
            countries (list): country codes of countries to plot
            ratio (array): ratio = FAO/ISIMIP crop production data of countries to plot
        Optional Parameters:
            save (boolean): True saves figure, else figure is not saved.
            output_dir (str): directory to save figure
        Returns:
            fig (plt figure handle)
            axes (plot axes handle)

    """
    fig = plt.figure()
    axes = plt.gca()
    axes.scatter(countries[ratio != 1], ratio[ratio != 1])
    axes.set_yscale('log')
    axes.set_ylabel('Ratio= FAO / ISIMIP')
    axes.set_xlabel('ISO3 country code')
    axes.set_ylim(np.nanmin(ratio), np.nanmax(ratio))
    plt.title(crop)

    if save:
        if not os.path.exists(os.path.join(output_dir, 'Exposure_norm_plots')):
            os.mkdir(os.path.join(output_dir, 'Exposure_norm_plots'))
        plt.savefig(os.path.join(output_dir, 'Exposure_norm_plots',
                                 'fig_ratio_norm_' + crop))
    return fig, axes
