// Google Earth Engine Code Editor script for the Sovereign Compute Nexus Ceará case study.
// Paste into https://code.earthengine.google.com/ and run.
// It requests Sentinel-2 optical indices and Sentinel-1 SAR composites for the 50 km x 50 km box.

var region = ee.Geometry.Polygon([
  [
    [-38.59542117897013, -3.78411760987379],
    [-38.59542117897013, -3.335827836801036],
    [-39.04457882102989, -3.335827836801036],
    [-39.04457882102989, -3.78411760987379],
    [-38.59542117897013, -3.78411760987379]
  ]
]);

var startDate = '2021-10-01';
var endDate = '2026-05-01';
var frequencyMonths = 3; // 1 for monthly, 3 for quarterly, 12 for annual.
var driveFolder = 'SCN_GEE_Sentinel_Ceara';
var s2CloudPct = 60;

Map.centerObject(region, 10);
Map.addLayer(region, {color: 'red'}, 'SCN Ceara 50km case-study box');

function maskS2Clouds(image) {
  var scl = image.select('SCL');
  var mask = scl.neq(3)
    .and(scl.neq(8))
    .and(scl.neq(9))
    .and(scl.neq(10))
    .and(scl.neq(11));

  var scaled = image.select(['B2', 'B3', 'B4', 'B8', 'B11', 'B12']).multiply(0.0001);
  var ndvi = scaled.normalizedDifference(['B8', 'B4']).rename('NDVI');
  var ndwi = scaled.normalizedDifference(['B3', 'B8']).rename('NDWI');
  var nbr = scaled.normalizedDifference(['B8', 'B12']).rename('NBR');
  return scaled.addBands([ndvi, ndwi, nbr])
    .updateMask(mask)
    .copyProperties(image, ['system:time_start']);
}

function sentinel2Composite(start, end) {
  return ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
    .filterBounds(region)
    .filterDate(start, end)
    .filter(ee.Filter.lte('CLOUDY_PIXEL_PERCENTAGE', s2CloudPct))
    .map(maskS2Clouds)
    .median()
    .select(['B2', 'B3', 'B4', 'B8', 'B11', 'B12', 'NDVI', 'NDWI', 'NBR'])
    .toFloat()
    .clip(region);
}

function sentinel1Composite(start, end) {
  var collection = ee.ImageCollection('COPERNICUS/S1_GRD')
    .filterBounds(region)
    .filterDate(start, end)
    .filter(ee.Filter.eq('instrumentMode', 'IW'))
    .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
    .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VH'))
    .select(['VV', 'VH']);

  var image = collection.median();
  var vv = image.select('VV');
  var vh = image.select('VH');
  return image
    .addBands(vv.subtract(vh).rename('VV_MINUS_VH'))
    .addBands(vv.divide(vh).rename('VV_DIV_VH'))
    .toFloat()
    .clip(region);
}

function pad2(n) {
  return n < 10 ? '0' + n : '' + n;
}

function formatDate(d) {
  return d.getUTCFullYear() + '-' + pad2(d.getUTCMonth() + 1) + '-' + pad2(d.getUTCDate());
}

function addMonthsClient(d, months) {
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + months, d.getUTCDate()));
}

var cursor = new Date(startDate + 'T00:00:00Z');
var endClient = new Date(endDate + 'T00:00:00Z');

while (cursor < endClient) {
  var next = addMonthsClient(cursor, frequencyMonths);
  if (next > endClient) {
    next = endClient;
  }

  var periodStart = formatDate(cursor);
  var periodEnd = formatDate(next);
  var label = periodStart.replace(/-/g, '') + '_' + periodEnd.replace(/-/g, '');
  var s2 = sentinel2Composite(periodStart, periodEnd);
  var s1 = sentinel1Composite(periodStart, periodEnd);

  Export.image.toDrive({
    image: s2,
    description: 'scn_ceara_s2_' + label,
    folder: driveFolder,
    fileNamePrefix: 'scn_ceara_s2_' + label,
    region: region,
    scale: 10,
    maxPixels: 1e13,
    fileFormat: 'GeoTIFF'
  });

  Export.image.toDrive({
    image: s1,
    description: 'scn_ceara_s1_' + label,
    folder: driveFolder,
    fileNamePrefix: 'scn_ceara_s1_' + label,
    region: region,
    scale: 10,
    maxPixels: 1e13,
    fileFormat: 'GeoTIFF'
  });

  cursor = next;
}

var previewS2 = sentinel2Composite('2024-07-01', '2024-10-01');
Map.addLayer(previewS2.select('NDVI'), {min: -0.2, max: 0.8, palette: ['brown', 'yellow', 'green']}, 'Preview Sentinel-2 NDVI');
var previewS1 = sentinel1Composite('2024-07-01', '2024-10-01');
Map.addLayer(previewS1.select('VV'), {min: -20, max: 0}, 'Preview Sentinel-1 VV');
