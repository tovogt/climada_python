"""
This file is part of CLIMADA.

Copyright (C) 2017 CLIMADA contributors listed in AUTHORS.

CLIMADA is free software: you can redistribute it and/or modify it under the
terms of the GNU Lesser General Public License as published by the Free
Software Foundation, version 3.

CLIMADA is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE.  See the GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License along
with CLIMADA. If not, see <https://www.gnu.org/licenses/>.

---

Test BlackMarble base class.
"""
import unittest
import numpy as np
import scipy.sparse as sparse
import shapely
from cartopy.io import shapereader
from sklearn.neighbors import DistanceMetric

from climada.entity.exposures.black_marble import country_iso_geom, BlackMarble, \
_process_country, fill_econ_indicators, _set_econ_indicators, _fill_admin1_geom, 
_cut_admin1, _resample_land
from climada.entity.exposures.nightlight import NOAA_BORDER, NOAA_RESOLUTION_DEG
from climada.util.constants import ONE_LAT_KM
from climada.util.coordinates import coord_on_land

SHP_FN = shapereader.natural_earth(resolution='10m', \
    category='cultural', name='admin_0_countries')
SHP_FILE = shapereader.Reader(SHP_FN)

ADM1_FILE = shapereader.natural_earth(resolution='10m',
                                      category='cultural',
                                      name='admin_1_states_provinces')
ADM1_FILE = shapereader.Reader(ADM1_FILE)

class TestCountryIso(unittest.TestCase):
    """Test country_iso function."""

    def test_che_kos_pass(self):
        """CHE, KOS """
        country_name = ['Switzerland', 'Kosovo']
        iso_name, _ = country_iso_geom(country_name, SHP_FILE)

        self.assertEqual(len(iso_name), len(country_name))
        self.assertTrue('CHE'in iso_name)
        self.assertTrue('KOS'in iso_name)
        self.assertEqual(iso_name['CHE'][0], 756)
        self.assertEqual(iso_name['CHE'][1], 'Switzerland')
        self.assertIsInstance(iso_name['CHE'][2], shapely.geometry.multipolygon.MultiPolygon)
        self.assertEqual(iso_name['KOS'][0], 0)
        self.assertEqual(iso_name['KOS'][1], 'Kosovo')
        self.assertIsInstance(iso_name['KOS'][2], shapely.geometry.multipolygon.MultiPolygon)

    def test_haiti_pass(self):
        """HTI"""
        country_name = ['HaITi']
        iso_name, _ = country_iso_geom(country_name, SHP_FILE)

        self.assertEqual(len(iso_name), len(country_name))
        self.assertEqual(iso_name['HTI'][0], 332)
        self.assertEqual(iso_name['HTI'][1], 'Haiti')
        self.assertIsInstance(iso_name['HTI'][2], shapely.geometry.multipolygon.MultiPolygon)

    def test_wrong_fail(self):
        """Wrong name"""
        country_name = ['Kasovo']
        with self.assertRaises(ValueError):
            country_iso_geom(country_name, SHP_FILE)

    def test_bolivia_pass(self):
        """BOL"""
        country_name = ['Bolivia']
        iso_name, _ = country_iso_geom(country_name, SHP_FILE)

        self.assertEqual(len(iso_name), len(country_name))
        self.assertEqual(iso_name['BOL'][0], 68)
        self.assertEqual(iso_name['BOL'][1], 'Bolivia')
        self.assertIsInstance(iso_name['BOL'][2], shapely.geometry.multipolygon.MultiPolygon)

    def test_korea_pass(self):
        """PRK"""
        country_name = ['Korea']
        with self.assertRaises(ValueError):
            country_iso_geom(country_name, SHP_FILE)

    def test_select_reg_pass(self):
        """Select counrty """
        exp = BlackMarble()
        exp.value = np.arange(0, 20, 0.1)
        exp.coord = np.ones((200, 2))
        exp.impact_id = {'TC': np.ones((200,))}
        exp.id = np.arange(200)

        exp.region_id = np.ones((200,))
        exp.region_id[:101] = exp.region_id[:101]*780
        exp.region_id[101:] = exp.region_id[101:]*894

        sel_exp = exp.select('TTO')
        self.assertEqual(sel_exp.value.size, 101)
        sel_exp = exp.select('Trinidad and Tobago')
        self.assertEqual(sel_exp.value.size, 101)
        sel_exp = exp.select('TT')
        self.assertEqual(sel_exp.value.size, 101)
        self.assertIsInstance(sel_exp, BlackMarble)

        sel_exp = exp.select('ZMB')
        self.assertEqual(sel_exp.value.size, 99)
        sel_exp = exp.select('Zambia')
        self.assertEqual(sel_exp.value.size, 99)
        sel_exp = exp.select('ZM')
        self.assertEqual(sel_exp.value.size, 99)
        self.assertIsInstance(sel_exp, BlackMarble)

    def test_select_not_included_pass(self):
        """Select country that is not contained."""
        exp = BlackMarble()
        exp.value = np.arange(0, 20, 0.1)
        exp.coord = np.ones((200, 2))
        exp.impact_id = {'TC': np.ones((200,))}
        exp.id = np.arange(200)

        exp.region_id = np.ones((200,))
        exp.region_id[:101] = exp.region_id[:101]*780
        exp.region_id[101:] = exp.region_id[101:]*894

        sel_exp = exp.select('UGA')
        self.assertEqual(sel_exp, None)

    def test_select_wrong_fail(self):
        """Select country that is not contained."""
        exp = BlackMarble()
        exp.value = np.arange(0, 20, 0.1)
        exp.coord = np.ones((200, 2))
        exp.impact_id = {'TC': np.ones((200,))}
        exp.id = np.arange(200)

        exp.region_id = np.ones((200,))
        exp.region_id[:101] = exp.region_id[:101]*780
        exp.region_id[101:] = exp.region_id[101:]*894

        sel_exp = exp.select('TTU')
        self.assertEqual(sel_exp, None)

class TestProvinces(unittest.TestCase):
    """Tst black marble with admin1."""
    def test_fill_admin1_geom_pass(self):
        """Test function _fill_admin1_geom pass."""
        iso3 = 'ESP'
        admin1_rec = list(ADM1_FILE.records())

        prov_list = ['Barcelona']
        res_bcn = _fill_admin1_geom(iso3, admin1_rec, prov_list)
        self.assertEqual(len(res_bcn), 1)
        self.assertIsInstance(res_bcn[0], shapely.geometry.multipolygon.MultiPolygon)

        prov_list = ['Barcelona', 'Tarragona']
        res_bcn = _fill_admin1_geom(iso3, admin1_rec, prov_list)
        self.assertEqual(len(res_bcn), 2)
        self.assertIsInstance(res_bcn[0], shapely.geometry.multipolygon.MultiPolygon)
        self.assertIsInstance(res_bcn[1], shapely.geometry.multipolygon.MultiPolygon)

    def test_fill_admin1_geom_fail(self):
        """Test function _fill_admin1_geom fail."""
        iso3 = 'CHE'
        admin1_rec = list(ADM1_FILE.records())

        prov_list = ['Barcelona']
        with self.assertRaises(ValueError):
            with self.assertLogs('climada.entity.exposures.black_marble', level='ERROR') as cm:
                _fill_admin1_geom(iso3, admin1_rec, prov_list)
        self.assertIn('Barcelona not found. Possible provinces of CHE are: ', cm.output[0])

    def test_country_iso_geom_pass(self):
        """Test country_iso_geom pass."""
        countries = ['Switzerland']
        _, cntry_admin1 = country_iso_geom(countries, SHP_FILE)
        self.assertEqual(cntry_admin1, {'CHE': []})
        self.assertIsInstance(countries, list)

        countries = {'Switzerland': ['Zürich']}
        _, cntry_admin1 = country_iso_geom(countries, SHP_FILE)
        self.assertEqual(len(cntry_admin1['CHE']), 1)
        self.assertIsInstance(cntry_admin1['CHE'][0], shapely.geometry.multipolygon.MultiPolygon)

    def test_filter_admin1_pass(self):
        """Test _cut_admin1 pass."""
        lat, lon = np.mgrid[35 : 44 : complex(0, 100),
                             0 : 4 : complex(0, 102)]
        nightlight = np.arange(102*100).reshape((102, 100))

        coord_nl = np.array([[35, 0.09090909], [0, 0.03960396]])
        on_land = np.zeros((100, 102), bool)

        admin1_rec = list(ADM1_FILE.records())
        for rec in admin1_rec:
            if 'Barcelona' in rec.attributes['name']:
                bcn_geom = rec.geometry
            if 'Tarragona' in rec.attributes['name']:
                tar_geom = rec.geometry
        admin1_geom = [bcn_geom, tar_geom]

        nightlight_reg, lat_reg, lon_reg, all_geom, on_land_reg = _cut_admin1(
                nightlight, lat, lon, admin1_geom, coord_nl, on_land)

        self.assertEqual(lat_reg.shape, lon_reg.shape)
        self.assertEqual(lat_reg.shape, nightlight_reg.shape)
        self.assertEqual(lat_reg.shape, on_land_reg.shape)
        self.assertTrue(np.array_equal(nightlight[60:82, 4:72], nightlight_reg))
        for coord in zip(lat_reg[on_land_reg], lon_reg[on_land_reg]):
            self.assertTrue(bcn_geom.contains(shapely.geometry.Point([coord[1],coord[0]])) or
                            tar_geom.contains(shapely.geometry.Point([coord[1],coord[0]])))
            self.assertTrue(all_geom.contains(shapely.geometry.Point([coord[1],coord[0]])))

class TestNightLight(unittest.TestCase):
    """Test nightlight functions."""

    def test_process_country_brb_1km_pass(self):
        """ Test _process_country function with fake Barbados."""
        country_iso = 'BRB'
        for cntry in list(SHP_FILE.records()):
            if cntry.attributes['ADM0_A3'] == country_iso:
                geom = cntry.geometry
        nightlight = sparse.lil.lil_matrix(np.ones((500, 1000)))
        nightlight[275:281, 333:334] = 0.4
        nightlight[275:281, 334:336] = 0.5
        nightlight = nightlight.tocsr()

        coord_nl = np.empty((2, 2))
        coord_nl[0, :] = [NOAA_BORDER[1]+NOAA_RESOLUTION_DEG,
                          0.2805444221776838]
        coord_nl[1, :] = [NOAA_BORDER[0]+NOAA_RESOLUTION_DEG,
                          0.3603520186853473]

        nightlight_reg, lat_reg, lon_reg, on_land = _process_country(geom, nightlight, coord_nl)

        lat_ref = np.array([[12.9996827 , 12.9996827 , 12.9996827 ],
                              [13.28022712, 13.28022712, 13.28022712],
                              [13.56077154, 13.56077154, 13.56077154]])
        lon_ref = np.array([[-59.99444444, -59.63409243, -59.27374041],
                              [-59.99444444, -59.63409243, -59.27374041],
                              [-59.99444444, -59.63409243, -59.27374041]])
        on_ref = np.array([[False, False, False],
                            [False,  True, False],
                            [False, False, False]])

        in_lat = (278, 280)
        in_lon = (333, 335)
        nightlight_ref = nightlight[in_lat[0]:in_lat[1]+1, in_lon[0]:in_lon[1]+1].todense()
        nightlight_ref[np.logical_not(on_ref)] = 0.0

        self.assertTrue(np.allclose(lat_ref, lat_reg))
        self.assertTrue(np.allclose(lon_ref, lon_reg))
        self.assertTrue(np.allclose(on_ref, on_land))
        self.assertTrue(np.allclose(nightlight_ref, nightlight_reg))

    def test_process_country_brb_2km_pass(self):
        """ Test _resample_land function with fake Barbados."""
        country_iso = 'BRB'
        for cntry in list(SHP_FILE.records()):
            if cntry.attributes['ADM0_A3'] == country_iso:
                geom = cntry.geometry
        nightlight = sparse.lil.lil_matrix(np.ones((500, 1000)))
        nightlight[275:281, 333:334] = 0.4
        nightlight[275:281, 334:336] = 0.5
        nightlight = nightlight.tocsr()

        coord_nl = np.empty((2, 2))
        coord_nl[0, :] = [NOAA_BORDER[1]+NOAA_RESOLUTION_DEG,
                          0.2805444221776838]
        coord_nl[1, :] = [NOAA_BORDER[0]+NOAA_RESOLUTION_DEG,
                          0.3603520186853473]

        res_fact = 2.0
        nightlight_reg, lat_reg, lon_reg, on_land = _process_country(geom, nightlight, coord_nl)
        nightlight_res, lat_res, lon_res = _resample_land(geom, nightlight_reg, lat_reg, lon_reg, res_fact, on_land)

        lat_ref = np.array([[12.9996827, 12.9996827, 12.9996827, 12.9996827, 12.9996827, 12.9996827 ],
                              [13.11190047, 13.11190047, 13.11190047, 13.11190047, 13.11190047, 13.11190047],
                              [13.22411824, 13.22411824, 13.22411824, 13.22411824, 13.22411824, 13.22411824],
                              [13.33633601, 13.33633601, 13.33633601, 13.33633601, 13.33633601, 13.33633601],
                              [13.44855377, 13.44855377, 13.44855377, 13.44855377, 13.44855377, 13.44855377],
                              [13.56077154, 13.56077154, 13.56077154, 13.56077154, 13.56077154, 13.56077154]])

        lon_ref = np.array([[-59.99444444, -59.85030364, -59.70616283, -59.56202202, -59.41788121, -59.27374041],
                              [-59.99444444, -59.85030364, -59.70616283, -59.56202202, -59.41788121, -59.27374041],
                              [-59.99444444, -59.85030364, -59.70616283, -59.56202202, -59.41788121, -59.27374041],
                              [-59.99444444, -59.85030364, -59.70616283, -59.56202202, -59.41788121, -59.27374041],
                              [-59.99444444, -59.85030364, -59.70616283, -59.56202202, -59.41788121, -59.27374041],
                              [-59.99444444, -59.85030364, -59.70616283, -59.56202202, -59.41788121, -59.27374041]])

        on_ref = np.array([[False, False, False, False, False, False],
                            [False, False, False,  True, False, False],
                            [False, False, False,  True, False, False],
                            [False, False, False, False, False, False],
                            [False, False, False, False, False, False],
                            [False, False, False, False, False, False]])

        self.assertTrue(np.allclose(lat_ref[on_ref], lat_res))
        self.assertTrue(np.allclose(lon_ref[on_ref], lon_res))
        self.assertAlmostEqual(nightlight_res[0], 0.1410256410256411)
        self.assertAlmostEqual(nightlight_res[1], 0.3589743589743589)

    def test_add_sea_pass(self):
        """Test add_sea function with fake data."""
        exp = BlackMarble()

        exp.value = np.arange(0, 1.0e6, 1.0e5)

        min_lat, max_lat = 27.5, 30
        min_lon, max_lon = -18, -12
        exp.coord = np.zeros((10, 2))
        exp.coord[:, 0] = np.linspace(min_lat, max_lat, 10)
        exp.coord[:, 1] = np.linspace(min_lon, max_lon, 10)
        exp.region_id = np.ones(10)

        sea_coast = 100
        sea_res_km = 50
        sea_res = (sea_coast, sea_res_km)
        exp.add_sea(sea_res)
        exp.check()

        sea_coast /= ONE_LAT_KM
        sea_res_km /= ONE_LAT_KM

        min_lat = min_lat - sea_coast
        max_lat = max_lat + sea_coast
        min_lon = min_lon - sea_coast
        max_lon = max_lon + sea_coast
        self.assertEqual(np.min(exp.coord.lat), min_lat)
        self.assertEqual(np.min(exp.coord.lon), min_lon)
        self.assertTrue(np.array_equal(exp.value[:10], np.arange(0, 1.0e6, 1.0e5)))

        on_sea_lat = exp.coord[11:, 0]
        on_sea_lon = exp.coord[11:, 1]
        res_on_sea = coord_on_land(on_sea_lat, on_sea_lon)
        res_on_sea = np.logical_not(res_on_sea)
        self.assertTrue(np.all(res_on_sea))

        dist = DistanceMetric.get_metric('haversine')
        self.assertAlmostEqual(dist.pairwise([[exp.coord[-1][1], exp.coord[-1][0]],
            [exp.coord[-2][1], exp.coord[-2][0]]])[0][1], sea_res_km)

class TestEconIndices(unittest.TestCase):
    """Test functions to get economic indices."""

    def test_fill_econ_indicators_pass(self):
        """ Test fill_econ_indicators CHE, ZMB."""
        ref_year = 2015
        country_isos = {'CHE': [1, 'Switzerland', 'che_geom'],
                        'ZMB': [2, 'Zambia', 'zmb_geom']
                       }
        fill_econ_indicators(ref_year, country_isos, SHP_FILE)
        country_isos_ref = {'CHE': [1, 'Switzerland', 'che_geom', 2015, 679289166858.236, 4],
                            'ZMB': [2, 'Zambia', 'zmb_geom', 2015, 21154394545.895, 2]
                           }
        self.assertEqual(country_isos, country_isos_ref)

    def test_fill_econ_indicators_kwargs_pass(self):
        """ Test fill_econ_indicators with kwargs inputs."""
        ref_year = 2015
        country_isos = {'CHE': [1, 'Switzerland', 'che_geom'],
                        'ZMB': [2, 'Zambia', 'zmb_geom']
                       }
        gdp = {'CHE': 1.2, 'ZMB': 1.3}
        inc_grp = {'CHE': 3, 'ZMB': 4}
        kwargs = {'gdp': gdp, 'inc_grp': inc_grp}
        fill_econ_indicators(ref_year, country_isos, SHP_FILE, **kwargs)
        country_isos_ref = {'CHE': [1, 'Switzerland', 'che_geom', 2015, gdp['CHE'], inc_grp['CHE']],
                            'ZMB': [2, 'Zambia', 'zmb_geom', 2015, gdp['ZMB'], inc_grp['ZMB']]
                           }
        self.assertEqual(country_isos, country_isos_ref)

    def test_fill_econ_indicators_na_pass(self):
        """ Test fill_econ_indicators with '' inputs."""
        ref_year = 2015
        country_isos = {'CHE': [1, 'Switzerland', 'che_geom'],
                        'ZMB': [2, 'Zambia', 'zmb_geom']
                       }
        gdp = {'CHE': 1.2, 'ZMB': ''}
        inc_grp = {'CHE': '', 'ZMB': 4}
        kwargs = {'gdp': gdp, 'inc_grp': inc_grp}
        fill_econ_indicators(ref_year, country_isos, SHP_FILE, **kwargs)
        country_isos_ref = {'CHE': [1, 'Switzerland', 'che_geom', 2015, gdp['CHE'], 4],
                            'ZMB': [2, 'Zambia', 'zmb_geom', 2015, 21154394545.895, inc_grp['ZMB']]
                           }
        self.assertEqual(country_isos, country_isos_ref)

    def test_set_econ_indicators_pass(self):
        """ Test _set_econ_indicators pass."""
        nightlight = np.arange(0, 20, 0.1).reshape((100, 2))
        gdp = 4.225e9
        inc_grp = 4
        nightlight = _set_econ_indicators(nightlight, gdp, inc_grp, [0, 0, 1])

        self.assertAlmostEqual(nightlight.sum(), gdp*(inc_grp+1), 5)

# Execute Tests
TESTS = unittest.TestLoader().loadTestsFromTestCase(TestEconIndices)
TESTS.addTests(unittest.TestLoader().loadTestsFromTestCase(TestCountryIso))
TESTS.addTests(unittest.TestLoader().loadTestsFromTestCase(TestNightLight))
TESTS.addTests(unittest.TestLoader().loadTestsFromTestCase(TestProvinces))
unittest.TextTestRunner(verbosity=2).run(TESTS)
