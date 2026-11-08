# Run Heatmap

Drop in your files from Strava, Garmin, or another source and every route is drawn on a map. The routes you run more frequently glow brightest.

### Get your GPS files

| Service | How to export |
|---|---|
| **Strava** | Settings → My Account → *Download or Delete Your Account* → *Request your archive*. Drop in the `.zip`. |
| **Garmin Connect** | Account settings → *Data Management* → *Export Your Data*. Drop in the `.zip` as it is. |
| **Apple Health** | Health app → profile picture → *Export All Health Data*. Unzip it and drop in `apple_health_export/workout-routes`. |
| **Others** (Coros, Polar, Suunto, …) | Any GPX, TCX or FIT files work. |

### London boroughs

If any of your runs are in London, a **London boroughs** panel appears. It counts
how many of the 32 boroughs you've run in (the City of London is listed too, but
isn't one of the 32), overlays the borough boundaries on the map with the ones
you've visited in green, and shows the distance you've covered in each. Hover a
borough on the map, or click one in the list, to see it. It follows the filters
in step 2, so you can count runs only, or a single year.

## Putting it on a web server

Clone the repository straight into your web server's document folder:

```sh
cd /path/to/htdocs            # or /var/www/html, etc.
git clone https://github.com/woodyjeffay/heatmap.git heatmap
```

Then open `http://localhost/heatmap/` (or `https://your-domain/heatmap/`).
To update later, run `git pull` inside that folder.

It has to come from a web server. Double-clicking `index.html` won't work,
because browsers only run this kind of page (JavaScript modules) when it
comes from a server. To test without Apache, run this in the folder and
open http://localhost:8000:

```sh
python3 -m http.server 8000
```

## Street map API keys

The default map, Stadia, does not require an API key. However CARTO and MapTiler do.

1. Get your free key here:

| Map | Key |
|---|---|
| CARTO dark / light *(default)* | Free key from [carto.com](https://carto.com/basemaps/apikey/). |
| MapTiler dark | Free key from [maptiler.com](https://cloud.maptiler.com/account/keys/). |

2. Paste it into [`config.js`](config.js):

   ```js
   mapKeys: {
     carto: "paste-your-carto-key-here",
     maptiler: "paste-your-maptiler-key-here",
   },
   ```

## Files

```
index.html        the page
config.js         map API keys and the default map (edit this one)
css/app.css
js/
├── app.js        page wiring
├── parse.js      GPX / TCX / FIT / zip / Strava csv reading
├── geo.js        map projection and framing
├── map.js        the interactive glowing map
├── heat.js       poster rendering
├── boroughs.js   which London boroughs you've run in
└── london-boroughs.js  borough boundaries (from OpenStreetMap)
vendor/           Leaflet (maps) and fflate (unzipping)
```

## Browser notes

- Works in current Chrome, Edge, Firefox and Safari.
- Big posters use a lot of memory. A2 at 300 dpi is about 35 million pixels.
  On phones and iPads, Safari may refuse sizes that large, and the page will
  ask for a lower quality.
- Very large exports (several GB) work best added as an unzipped folder.
