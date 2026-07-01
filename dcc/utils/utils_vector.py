import sys, os, time, json, math, pickle
from shapely.geometry import shape
import shapely
import pyproj
from functools import partial
import geopandas as gpd
import numpy as np

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())
sys.path.append(os.environ.get("PROJECT_ROOT"))


def lat_lon_z_to_x_y(lat_deg, lon_deg, zoom):
    # copied from: https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames#Python
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return (xtile, ytile)


def x_y_z_to_lat_lon(xtile, ytile, zoom): \
        # copied from: https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames#Python
    # This returns the NW-corner of the square. Use the function with xtile+1 and/or ytile+1 to get the other corners. With xtile+0.5 & ytile+0.5 it will return the center of the tile.
    n = 2.0 ** zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return (lat_deg, lon_deg)


def create_circle_using_aep(lat, lon, radius):
    # create_circle_using_azimuthal_equidistant_projection

    # radius in meters
    local_azimuthal_projection = "+proj=aeqd +R=6371000 +units=m +lat_0={} +lon_0={}".format(
        lat, lon
    )
    wgs84_to_aeqd = partial(
        pyproj.transform,
        pyproj.Proj("+proj=longlat +datum=WGS84 +no_defs"),
        pyproj.Proj(local_azimuthal_projection),
    )
    aeqd_to_wgs84 = partial(
        pyproj.transform,
        pyproj.Proj(local_azimuthal_projection),
        pyproj.Proj("+proj=longlat +datum=WGS84 +no_defs"),
    )

    center = shapely.geometry.Point(float(lon), float(lat))
    point_transformed = shapely.ops.transform(wgs84_to_aeqd, center)
    buffer = point_transformed.buffer(radius)
    # Get the polygon with lat lon coordinates
    circle_poly = shapely.ops.transform(aeqd_to_wgs84, buffer)

    return circle_poly


def buffer_polygon_using_aep(geom, radius):
    # create_circle_using_azimuthal_equidistant_projection

    # radius in meters
    lon = geom.centroid.xy[0][0]
    lat = geom.centroid.xy[1][0]

    local_azimuthal_projection = "+proj=aeqd +R=6371000 +units=m +lat_0={} +lon_0={}".format(
        lat, lon
    )
    wgs84_to_aeqd = partial(
        pyproj.transform,
        pyproj.Proj("+proj=longlat +datum=WGS84 +no_defs"),
        pyproj.Proj(local_azimuthal_projection),
    )
    aeqd_to_wgs84 = partial(
        pyproj.transform,
        pyproj.Proj(local_azimuthal_projection),
        pyproj.Proj("+proj=longlat +datum=WGS84 +no_defs"),
    )

    geom_transformed = shapely.ops.transform(wgs84_to_aeqd, geom)
    buffer = geom_transformed.buffer(radius)
    # Get the polygon with lat lon coordinates
    buffer_geom = shapely.ops.transform(aeqd_to_wgs84, buffer)

    return buffer_geom


def geom_to_area(geom):
    crs_4326 = pyproj.CRS.from_epsg(4326)
    transformer = pyproj.Transformer.from_crs(
        crs_4326,
        pyproj.CRS(proj='aea',
                   lat_1=geom.bounds[1],
                   lat_2=geom.bounds[3]
                   )
    )
    geom_area = shapely.ops.transform(transformer.transform, geom)
    return geom_area.area


# def convert_tpk_to_mbtiles(output_tpk_path):
#     from tpkutils import TPK
#     with TPK(output_tpk_path) as tpk:
#         tpk.to_mbtiles(os.path.join(os.path.splitext(output_tpk_path)[0] + '.mbtiles'))
#         print(f'extracted {output_tpk_path} to mbtiles')


# def convert_tpk_to_img_files(output_tpk_path):
#     from tpkutils import TPK
#     scheme = 'xyz'
#     with TPK(output_tpk_path) as tpk:
#         tpk.to_disk(os.path.join(os.path.splitext(output_tpk_path)[0]), scheme=scheme, drop_empty=False)
#         print(f'extracted {output_tpk_path} to disk')


def generate_vector_grid(outputGridfn, xmin, xmax, ymin, ymax, gridHeight, gridWidth):
    from math import ceil
    from osgeo import ogr

    # get rows
    rows = ceil((ymax - ymin) / gridHeight)
    # get columns
    cols = ceil((xmax - xmin) / gridWidth)

    # start grid cell envelope
    ringXleftOrigin = xmin
    ringXrightOrigin = xmin + gridWidth
    ringYtopOrigin = ymax
    ringYbottomOrigin = ymax - gridHeight

    # create output file
    outDriver = ogr.GetDriverByName('ESRI Shapefile')
    if os.path.exists(outputGridfn):
        os.remove(outputGridfn)
    outDataSource = outDriver.CreateDataSource(outputGridfn)
    outLayer = outDataSource.CreateLayer(outputGridfn, geom_type=ogr.wkbPolygon)
    featureDefn = outLayer.GetLayerDefn()

    # create grid cells
    countcols = 0
    while countcols < cols:
        countcols += 1

        # reset envelope for rows
        ringYtop = ringYtopOrigin
        ringYbottom = ringYbottomOrigin
        countrows = 0

        while countrows < rows:
            countrows += 1
            ring = ogr.Geometry(ogr.wkbLinearRing)
            ring.AddPoint(ringXleftOrigin, ringYtop)
            ring.AddPoint(ringXrightOrigin, ringYtop)
            ring.AddPoint(ringXrightOrigin, ringYbottom)
            ring.AddPoint(ringXleftOrigin, ringYbottom)
            ring.AddPoint(ringXleftOrigin, ringYtop)
            poly = ogr.Geometry(ogr.wkbPolygon)
            poly.AddGeometry(ring)

            # add new geom to layer
            outFeature = ogr.Feature(featureDefn)
            outFeature.SetGeometry(poly)
            outLayer.CreateFeature(outFeature)
            outFeature.Destroy

            # new envelope for next poly
            ringYtop = ringYtop - gridHeight
            ringYbottom = ringYbottom - gridHeight

        # new envelope for next poly
        ringXleftOrigin = ringXleftOrigin + gridWidth
        ringXrightOrigin = ringXrightOrigin + gridWidth

    # Close DataSources
    outDataSource.Destroy()


def get_grid_by_country(country_code, dx, dy, output_folder_path, countries_file_path):
    import os, fiona
    from shapely.geometry import shape

    # config
    output_country_folder_path = os.path.join(output_folder_path, country_code)
    output_grid_all_path = os.path.join(output_country_folder_path, 'grid_all.shp')
    output_grid_filtered_path = os.path.join(output_country_folder_path, 'grid_filtered.shp')

    # setup
    os.makedirs(output_country_folder_path, exist_ok=True)

    # utility to use, if necessary
    def list_all_country_codes():
        with fiona.open(countries_file_path, 'r') as ds_countries:
            for ds_country in ds_countries:
                print(
                    f"country: {ds_country['properties']['ADMIN']}; ISO A3 code: {ds_country['properties']['ISO_A3']}")

    # list_all_country_codes()

    print('asdf')

    # find the country of interest from a collection of country shapefiles
    with fiona.open(countries_file_path, 'r') as ds_countries:
        for ds_country in ds_countries:
            if ds_country['properties']['ISO_A3'] == country_code:
                xmin, ymin, xmax, ymax = shape(ds_country['geometry']).bounds

                ## use this code to tune dx and dy when it is not hardcoded
                # num_tiles_across_larger_dim = 100
                # dx = (xmax - xmin) / num_tiles_across_larger_dim
                # dy = (ymax - ymin) / num_tiles_across_larger_dim
                # if dx > dy:
                #     dx = dy
                # if dy > dx:
                #     dy = dx

                ds_country_match = ds_country
                break

    # generate vector grid for bounding box around whole region
    generate_vector_grid(output_grid_all_path, xmin, xmax, ymin, ymax, dy, dx)

    # setup
    cell_id = 0
    schema = {'geometry': 'Polygon', 'properties': {'cell_id': 'int', 'ISO_A3': 'str'}}

    # filter only boxes that overlap
    with fiona.open(output_grid_filtered_path, 'w', driver='ESRI Shapefile', schema=schema) as output:
        for grid_cell in fiona.open(output_grid_all_path):
            if shape(grid_cell['geometry']).intersects(shape(ds_country_match['geometry'])):
                print(f"added cell: {cell_id} for ISO {ds_country_match['properties']['ISO_A3']}")
                prop = {'cell_id': cell_id, 'ISO_A3': ds_country_match['properties']['ISO_A3']}
                cell_id = cell_id + 1
                ## clipping to exact country border
                # output.write({'geometry': mapping(shape(grid_cell['geometry']).intersection(shape(ds_country_match['geometry']))), 'properties': prop})
                # not clipping, just filtering
                output.write({'geometry': grid_cell['geometry'], 'properties': prop})

    print(f'Defined grid cells for country {country_code}. Number of cells: {cell_id}')


def get_grid_by_indian_state(state_name, dx, dy, output_folder_path, india_states_file_path):
    import os, fiona
    from shapely.geometry import shape

    # config
    output_state_folder_path = os.path.join(output_folder_path, state_name.replace(' ', '_'))
    output_grid_all_path = os.path.join(output_state_folder_path, 'grid_all.shp')
    output_grid_filtered_path = os.path.join(output_state_folder_path, 'grid_filtered.shp')

    # setup
    os.makedirs(output_state_folder_path, exist_ok=True)

    # find the country of interest from a collection of country shapefiles
    with fiona.open(india_states_file_path, 'r') as ds_countries:
        for ds_country in ds_countries:
            if ds_country['properties']['ST_NM'] == state_name:
                xmin, ymin, xmax, ymax = shape(ds_country['geometry']).bounds

                ds_country_match = ds_country
                break

    # generate vector grid for bounding box around whole region
    generate_vector_grid(output_grid_all_path, xmin, xmax, ymin, ymax, dy, dx)

    # setup
    cell_id = 0
    schema = {'geometry': 'Polygon', 'properties': {'cell_id': 'int', 'ST_NM': 'str'}}

    # filter only boxes that overlap
    with fiona.open(output_grid_filtered_path, 'w', driver='ESRI Shapefile', schema=schema) as output:
        for grid_cell in fiona.open(output_grid_all_path):
            if shape(grid_cell['geometry']).intersects(shape(ds_country_match['geometry'])):
                print(f"added cell: {cell_id} for State {ds_country_match['properties']['ST_NM']}")
                prop = {'cell_id': cell_id, 'ST_NM': ds_country_match['properties']['ST_NM']}
                cell_id = cell_id + 1
                ## clipping to exact country border
                # output.write({'geometry': mapping(shape(grid_cell['geometry']).intersection(shape(ds_country_match['geometry']))), 'properties': prop})
                # not clipping, just filtering
                output.write({'geometry': grid_cell['geometry'], 'properties': prop})

    print(f'Defined grid cells for state {state_name}. Number of cells: {cell_id}')


def get_grid_by_continent(continent_name, dx, dy, output_folder_path, continent_file_path):
    import os, fiona
    from shapely.geometry import shape

    # config
    output_continent_folder_path = os.path.join(output_folder_path, continent_name.replace(' ', '_'))
    output_grid_all_path = os.path.join(output_continent_folder_path, 'grid_all.shp')
    output_grid_filtered_path = os.path.join(output_continent_folder_path, 'grid_filtered.shp')

    # setup
    os.makedirs(output_continent_folder_path, exist_ok=True)

    # utility to use, if necessary
    def list_all_continent_names():
        with fiona.open(continent_file_path, 'r') as ds_countries:
            for ds_continent in ds_countries:
                print(
                    f"continent: {ds_continent['properties']['CONTINENT']}")

    # list_all_continent_names()

    print('asdf')

    # find the continent of interest from a collection of continent shapefiles
    with fiona.open(continent_file_path, 'r') as ds_countries:
        for ds_continent in ds_countries:
            if ds_continent['properties']['CONTINENT'] == continent_name:
                xmin, ymin, xmax, ymax = shape(ds_continent['geometry']).bounds

                ds_continent_match = ds_continent
                break

    # generate vector grid for bounding box around whole region
    generate_vector_grid(output_grid_all_path, xmin, xmax, ymin, ymax, dy, dx)

    # setup
    cell_id = 0
    schema = {'geometry': 'Polygon', 'properties': {'cell_id': 'int', 'CONTINENT': 'str'}}

    # filter only boxes that overlap
    with fiona.open(output_grid_filtered_path, 'w', driver='ESRI Shapefile', schema=schema) as output:
        for grid_cell in fiona.open(output_grid_all_path):
            if shape(grid_cell['geometry']).intersects(shape(ds_continent_match['geometry'])):
                print(f"added cell: {cell_id} for CONTINENT {ds_continent_match['properties']['CONTINENT']}")
                prop = {'cell_id': cell_id, 'CONTINENT': ds_continent_match['properties']['CONTINENT']}
                cell_id = cell_id + 1
                ## clipping to exact continent border
                # output.write({'geometry': mapping(shape(grid_cell['geometry']).intersection(shape(ds_continent_match['geometry']))), 'properties': prop})
                # not clipping, just filtering
                output.write({'geometry': grid_cell['geometry'], 'properties': prop})

    print(f'Defined grid cells for continent {continent_name}. Number of cells: {cell_id}')
    
    
    
def buffer_points_by_dist_in_m(points, dist_in_m=10):

    points_centroid = points.dissolve().centroid

    # Define the projection string for the azimuthal projection centered at the point
    proj_string = f"+proj=aeqd +lat_0={points_centroid.y} +lon_0={points_centroid.x} +x_0=0 +y_0=0"

    # Reproject the GeoDataFrame to the azimuthal projection
    points_azimuthal = points.to_crs(proj_string)

    # Buffer the point in the azimuthal projection
    buffered_polygons_azimuthal = points_azimuthal.buffer(dist_in_m)

    # Reproject the buffered polygon back to WGS84
    buffered_polygons_wgs84 = buffered_polygons_azimuthal.to_crs(
        "EPSG:4326")

    return buffered_polygons_wgs84


def get_bbox(shape):
    # clip OSM pbf to country boundary
    cntry_poly = gpd.read_file(shape).geometry[0]

    num_multipolygon_parts = gpd.GeoSeries(cntry_poly).explode(
        index_parts=True).shape[0]
    lat_top = np.max([
        np.max(
            gpd.GeoSeries(cntry_poly).explode(
                index_parts=True).iloc[w].exterior.coords.xy[1])
        for w in range(num_multipolygon_parts)
    ])
    lat_bottom = np.min([
        np.min(
            gpd.GeoSeries(cntry_poly).explode(
                index_parts=True).iloc[w].exterior.coords.xy[1])
        for w in range(num_multipolygon_parts)
    ])
    lon_left = np.min([
        np.min(
            gpd.GeoSeries(cntry_poly).explode(
                index_parts=True).iloc[w].exterior.coords.xy[0])
        for w in range(num_multipolygon_parts)
    ])
    lon_right = np.max([
        np.max(
            gpd.GeoSeries(cntry_poly).explode(
                index_parts=True).iloc[w].exterior.coords.xy[0])
        for w in range(num_multipolygon_parts)
    ])

    return lat_top, lat_bottom, lon_left, lon_right

def is_file_empty(filepath):
    if os.path.exists(filepath): 
        return os.stat(filepath).st_size == 0
    else:
        return True