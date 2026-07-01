import geopandas as gpd
from shapely.geometry import box
import numpy as np

class GeoJSONGridder:
    def __init__(self, filepath):
        """
        Initialize the class with the filepath of the GeoJSON.
        
        Parameters:
        filepath (str): Path to the GeoJSON file.
        """
        self.filepath = filepath
        self.data = self._load_geojson()

    def _load_geojson(self):
        """Load the GeoJSON file into a GeoDataFrame."""
        return gpd.read_file(self.filepath)

    def create_grid(self, cell_size_km=25):
        """
        Create a grid of squares over the GeoJSON shape.

        Parameters:
        cell_size_km (int): Size of each grid cell in kilometers (default is 25).

        Returns:
        GeoDataFrame: A GeoDataFrame containing the grid cells intersecting the shape.
        """
        cell_size_deg = cell_size_km / 111.32  # Convert km to degrees (approximation for lat/lon)
        bounds = self.data.total_bounds  # [minx, miny, maxx, maxy]
        minx, miny, maxx, maxy = bounds

        # Create grid cells
        x_coords = np.arange(minx, maxx, cell_size_deg)
        y_coords = np.arange(miny, maxy, cell_size_deg)

        grid_cells = []
        for x in x_coords:
            for y in y_coords:
                grid_cells.append(box(x, y, x + cell_size_deg, y + cell_size_deg))

        # Convert grid to GeoDataFrame
        grid = gpd.GeoDataFrame(grid_cells, columns=['geometry'], crs=self.data.crs)

        # Intersect grid with the original shape
        grid = gpd.overlay(grid, self.data, how='intersection')

        return grid

    def save_grid(self, output_filepath, cell_size_km=25):
        """
        Create and save the grid to a GeoJSON file.

        Parameters:
        output_filepath (str): Path to save the output GeoJSON file.
        cell_size_km (int): Size of each grid cell in kilometers (default is 25).
        """
        grid = self.create_grid(cell_size_km)
        grid.to_file(output_filepath, driver='GeoJSON')

# Example usage:
# gridder = GeoJSONGridder("path_to_geojson.geojson")
# grid = gridder.create_grid()
# gridder.save_grid("output_grid.geojson")
