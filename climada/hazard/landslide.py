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

---
Define Landslide class.
"""

__all__ = ['Landslide']

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy import sparse
from scipy.stats import binom, poisson
import shapely

from climada.hazard.base import Hazard
from climada.util.coordinates import (mapping_point2grid, 
                                      mapping_grid2flattened,
                                      read_raster)

LOGGER = logging.getLogger(__name__)
HAZ_TYPE = 'LS'

def sample_events_from_probs(prob_matrix, n_years, dist='binom'):
    """sample an event set for a specified representative time span from
    a matrix with annual occurrence probabilities 
    Draws events from chosen distribution.

    Parameters
    ----------
    prob_matrix : scipy.sparse.csr matrix
        matrix where each entry has an annual probability [0,1] of occurrence of 
        an event
    n_years : int
        the timespan of the probabilistic simulation in years
    dist : str
        distribution to sample from. currently 'binom' (default) and 'poisson'

    Returns
    -------
    ev_matrix : scipy.sparse.csr matrix
        csr matrix with number of success (events) from sampling process per 
        grid cell

    See also
    --------
    set_ls_prob(), scipy.stats.binom.rvs(), scipy.stats.poisson.rvs()

    """
    LOGGER.info('Sampling landslide events for a %i year period' % n_years)
    
    ev_matrix = prob_matrix.copy()
    
    if dist == 'binom':
        ev_matrix.data = binom.rvs(n=n_years, p=prob_matrix.data)
    if dist == 'poisson':
        # λ (or μ in scipy)
        mu = prob_matrix.data * n_years
        ev_matrix.data = poisson.rvs(mu)
        
    return ev_matrix


class Landslide(Hazard):
    """Landslide Hazard set generation.
    Attributes:
    """

    def __init__(self):
        """Empty constructor."""
        Hazard.__init__(self, HAZ_TYPE)

    def set_ls_hist(self, bbox, path_sourcefile, res=0.0083333):
        """
        Set historic landslide (ls) raster hazard from historical point records,
        for example as can be retrieved from the NASA COOLR initiative,
        which is the largest global ls repository, for a specific geographic
        extent.
        Points are assigned to the gridcell they fall into, and the whole grid-
        cell hence counts as equally affected.
        Event frequencies from an incomplete dataset are not meaningful and 
        hence aren't set by default. probabilistic calculations! Use the probabilistic method for this!
        
        See tutorial for details; the global ls catalog from NASA COOLR can be
        downloaded from https://maps.nccs.nasa.gov/arcgis/apps/webappviewer/index.html?id=824ea5864ec8423fb985b33ee6bc05b7

        Parameters:
        ----------
        bbox : tuple
            (minx, miny, maxx, maxy) geographic extent of interest
         path_sourcefile : str
             path to shapefile (.shp) with ls point data
        res : float
            resolution in degrees of the final grid cells which are created.
            (1° ~ 111 km at the equator). 
            Affects the hazard extent! Default is 0.00833
            
        Returns:
        --------
            self (Landslide() inst.): instance filled with historic LS hazard
                set for either point hazards or polygons with specified
                surrounding extent.
        """
        ls_gdf_bbox = gpd.read_file(path_sourcefile, bbox=bbox)
        
        self.centroids.set_raster_from_pnt_bounds(bbox,res)

        n_ev = len(ls_gdf_bbox)
        
        # assign lat-lon points of LS events to corresponding grid & flattened
        # grid-index
        ls_gdf_bbox = ls_gdf_bbox.assign(row=np.nan, col=np.nan)
        ls_gdf_bbox[['col', 'row']] = ls_gdf_bbox.apply(
            lambda row: mapping_point2grid(row.geometry, bbox[-1], bbox[0], 
                                           res), axis = 1).tolist()
        ls_gdf_bbox['flat_ix'] = ls_gdf_bbox.apply(
            lambda row: mapping_grid2flattened(row.col, row.row, 
                                               self.centroids.shape), axis = 1)        
        self.intensity = sparse.csr_matrix(
            (np.ones(n_ev), (np.arange(n_ev), ls_gdf_bbox.flat_ix)), 
            shape=(n_ev, self.centroids.size))
        self.fraction = self.intensity.copy()
        self.frequency = self.intensity.copy()
        self.frequency[self.frequency.nonzero()] = np.nan        
        self.units = ''
        self.event_id = np.arange(n_ev, dtype=int) + 1
        self.orig = np.ones(n_ev, bool)
        if hasattr(ls_gdf_bbox, 'ev_date'):
            self.date = pd.to_datetime(ls_gdf_bbox.ev_date, yearfirst=True)
        else:
            LOGGER.info('No event dates set from source')

        self.check()


    def set_ls_prob(self, bbox, path_sourcefile, corr_fact=10e6, n_years=500,
                    dist='poisson'):
        """
        Set probabilistic landslide hazard (fraction, intensity and
        frequency) for a defined bounding box and time period from a raster.
        The hazard data for which this function is explicitly written
        is readily provided by UNEP & the Norwegian Geotechnical Institute
        (NGI), and can be downloaded and unzipped from
        https://preview.grid.unep.ch/index.php?preview=data&events=landslides&evcat=2&lang=eng
        for precipitation-triggered landslide and from
        https://preview.grid.unep.ch/index.php?preview=data&events=landslides&evcat=1&lang=eng
        for earthquake-triggered landslides.
        It works of course with any similar raster file.
        Original data is given in expected annual probability and percentage
        of pixel of occurrence of a potentially destructive landslide event
        x 1000000 (so be sure to adjust this by setting the correction factor).
        More details can be found in the landslide tutorial and under above-
        mentioned links.

        Events are sampled from annual occurrence probabilites via binomial or 
        poisson distribution; intensity takes a binary value (0 - no
        ls occurrence; 1 - ls occurrence) and
        fraction stores the actual the occurrence count (0 to n) per grid cell.
        Frequency is occurrence count / n_years.

        Impact functions, since they act on the intensity, should hence be in
        the form of a step function,
        defining impact for intensity 0 and (close to) 1.

        Parameters
        ----------
        bbox : tuple
            (minx, miny, maxx, maxy) geographic extent of interest
        path_sourcefile : str
            path to UNEP/NGI ls hazard file (.tif)
        corr_fact : float or int
            factor by which to divide the values in the original probability
            file, in case it is not scaled to [0,1]. Default is 1'000'000
        n_years : int
            sampling period
        dist : str
        distribution to sample from. currently 'binom' (default) and 'poisson'

        Returns
        -------
        self : climada.hazard.Landslide instance
            probabilistic LS hazard
            
        See also
        --------
        sample_events_from_probs()
        """

        # raster with occurrence probs
        self.centroids.meta, prob_matrix = \
        read_raster(path_sourcefile, 
                    geometry=[shapely.geometry.box(*bbox, ccw=True)])
        prob_matrix = sparse.csr_matrix(prob_matrix.squeeze()/corr_fact)
        
        # sample events from probabilities
        self.fraction = sample_events_from_probs(prob_matrix, n_years, dist)
            
        # set frequ. to no. of occurrences per cell / timespan:
        self.frequency = self.fraction/n_years
        
        # set intensity to 1 wherever an occurrence:
        self.intensity = self.fraction.copy()
        self.intensity[self.intensity.nonzero()]=1       

        # meaningless, such that check() method passes:
        self.date = np.array([])
        self.event_name = []
        
        self.centroids.set_geometry_points()
        self.check()


