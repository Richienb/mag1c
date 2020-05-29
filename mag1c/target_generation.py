# Unit Absorption Spectrum Generation
# Markus Foote. 2020
import numpy as np
import scipy.ndimage
import argparse
import spectral

@np.vectorize
def get_5deg_zenith_angle_index(zenith_value):
    return zenith_value / 5

@np.vectorize
def get_5deg_sensor_height_index(sensor_value): # [1, 2, 4, 10, 20, 200]
    # There's not really a pattern here, so just linearly interpolate between values -- piecewise linear
    if sensor_value < 1.0:
        return np.float64(0.0)
    elif sensor_value < 2.0:
        idx = sensor_value - 1.0
        return idx
    elif sensor_value < 4:
        return sensor_value / 2
    elif sensor_value < 10:
        return (sensor_value / 6) + (4.0 / 3.0)
    elif sensor_value < 20:
        return (sensor_value / 10) + 2
    elif sensor_value < 200:
        return (sensor_value / 180) + (35.0 / 9.0)
    else:
        return 5
    
@np.vectorize
def get_5deg_ground_altitude_index(ground_value): # [0, 0.5, 1.0, 2.0, 3.0]
    if ground_value < 1:
        return 2 * ground_value
    else:
        return 1 + ground_value
    
@np.vectorize
def get_5deg_water_vapor_index(water_value):
    return water_value

@np.vectorize
def get_5deg_methane_index(methane_value):
    if methane_value <= 0:
        return 0
    elif methane_value < 1000:
        return methane_value / 1000
    return np.log2(methane_value / 500)

def get_5deg_lookup_index(zenith=0, sensor=200, ground=0, water=0, methane=0):
    idx =  np.asarray([[get_5deg_zenith_angle_index(zenith)], 
                       [get_5deg_sensor_height_index(sensor)],
                       [get_5deg_ground_altitude_index(ground)],
                       [get_5deg_water_vapor_index(water)], 
                       [get_5deg_methane_index(methane)]])
    return idx

def spline_5deg_lookup(grid_data, zenith=0, sensor=200, ground=0, water=0, methane=0, order=1):
    coords = get_5deg_lookup_index(zenith=zenith, sensor=sensor, ground=ground, water=water, methane=methane)
    lookup = np.asarray([scipy.ndimage.map_coordinates(im, coordinates=coords, order=order, mode='nearest') for im in np.moveaxis(grid_data, 5, 0)])
    return lookup.squeeze()

def load_dataset():
    filename = 'dataset_noms.npz'
    datafile = np.load(filename)
    return datafile['grid_5deg_data'], datafile['grid_5deg_param'], datafile['wave']

def generate_library(methane_vals, zenith=0, sensor=200, ground=0, water=0, order=1):
    grid, params, wave = load_dataset()
    rads = np.empty((len(methane_vals), grid.shape[-1]))
    for i, ppmm in enumerate(methane_vals):
        rads[i, :] = spline_5deg_lookup(grid, zenith=zenith, sensor=sensor, ground=ground, water=water, methane=ppmm, order=order)
    return rads, wave

def generate_template_from_bands(centers, fwhm, params):
    """Calculate a unit absorption spectrum for methane by convolving with given band information.

    :param centers: wavelength values for the band centers, provided in nanometers.
    :param fwhm: full width half maximum for the gaussian kernel of each band.
    :return template: the unit absorption spectum
    """
    # import scipy.stats
    SCALING = 1e5
    centers = np.asarray(centers)
    fwhm = np.asarray(fwhm)
    if np.any(~np.isfinite(centers)) or np.any(~np.isfinite(fwhm)):
        raise RuntimeError('Band Wavelengths Centers/FWHM data contains non-finite data (NaN or Inf).')
    if centers.shape[0] != fwhm.shape[0]:
        raise RuntimeError('Length of band center wavelengths and band fwhm arrays must be equal.')
#     lib = spectral.io.envi.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ch4.hdr'),
#                                 os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ch4.lut'))
#     rads = np.asarray(lib.asarray()).squeeze()
#     wave = np.asarray(lib.bands.centers)
    concentrations = np.asarray([0, 500, 1000, 2000, 4000, 8000, 16000, 32000])
    rads, wave = generate_library(concentrations, **params)
    # sigma = fwhm / ( 2 * sqrt( 2 * ln(2) ) )  ~=  fwhm / 2.355
    sigma = fwhm / (2.0 * np.sqrt(2.0 * np.log(2.0)))
    # response = scipy.stats.norm.pdf(wave[:, None], loc=centers[None, :], scale=sigma[None, :])
    # Evaluate normal distribution explicitly
    var = sigma ** 2
    denom = (2 * np.pi * var) ** 0.5
    numer = np.exp(-(wave[:, None] - centers[None, :])**2 / (2*var))
    response = numer / denom
    # Normalize each gaussian response to sum to 1.
    response = np.divide(response, response.sum(axis=0), where=response.sum(axis=0) > 0, out=response)
    # implement resampling as matrix multiply
    resampled = rads.dot(response)
    lograd = np.log(resampled, out=np.zeros_like(resampled), where=resampled > 0)
    slope, _, _, _ = np.linalg.lstsq(np.stack((np.ones_like(concentrations), concentrations)).T, lograd, rcond=None)
    spectrum = slope[1, :] * SCALING
    target = np.stack((np.arange(1, spectrum.shape[0]+1), centers, spectrum)).T
    return target



def main():
    parser = argparse.ArgumentParser(description='Create a unit absorption spectrum for specified parameters.')
    parser.add_argument('-z', '--zenith_angle', type=float, required=True, help='Zenith Angle (in degrees) for generated spectrum.')
    parser.add_argument('-s', '--sensor_height', type=float, required=True, help='Sensor Height (in km) above ground.')
    parser.add_argument('-g', '--ground_elevation', type=float, required=True, help='Ground Elevation (in km).')
    parser.add_argument('-w', '--water_vapor', type=float, required=True, help='Column water vapor (in cm).')
    parser.add_argument('--order', choices=(1,3), default=1, type=int, required=False, help='Spline interpolation degree.')
    parser.add_argument('--hdr', type=str, required=True, help='Header file for the flightline to match band centers/fwhm.')
    parser.add_argument('-o', '--output', type=str, default='generated_uas.txt', help='Output file to save spectrum.')
    args = parser.parse_args()
    param = {'zenith':args.zenith_angle, 
             'sensor':args.sensor_height,
             'ground':args.ground_elevation,
             'water':args.water_vapor,
             'order':args.order}
    image = spectral.io.envi.open(args.hdr)
    centers = image.bands.centers
    fwhm = image.bands.bandwidths
    uas = generate_template_from_bands(centers, fwhm, param)
    np.savetxt(args.output, uas, delimiter=' ', fmt=('%03d','% 10.3f','%.18f'))
    
    
    
if __name__ == '__main__':
    main()
