# Run Heatmap: website

A web page where people add their GPS files and see every run they've done
glow on one map, then make a poster to download.

It's a folder of static files: no PHP, no database, nothing to install on
the server. Everything happens in the visitor's browser, so their files are
**never uploaded**. Your server only sends them the page.

## Put it on your web server

1. Copy **everything inside this `website` folder** into your web server's
   document folder, e.g. `htdocs` for Apache/XAMPP/MAMP, `/var/www/html`, or
   a subfolder such as `htdocs/heatmap/`.
2. Open it through the web server: `http://localhost/` (or
   `http://localhost/heatmap/`), or your domain.

Opening `index.html` by double-clicking it won't work: browsers only run
this kind of page (JavaScript modules) when it comes from a web server. For a
quick test without Apache, run `python3 -m http.server 8000` inside this
folder and open http://localhost:8000.

```
website/
├── index.html        the page
├── config.js         your map API keys and the default map
├── css/app.css
├── js/
│   ├── app.js        page wiring
│   ├── parse.js      GPX / TCX / FIT / zip / Strava csv reading
│   ├── geo.js        map projection and framing
│   ├── map.js        the interactive glowing map
│   └── heat.js       poster rendering
└── vendor/           Leaflet (maps) and fflate (unzipping), included so
                      nothing is loaded from other websites
```

## What visitors can do

1. **Add runs**: drop a folder or `.zip` onto the page, or use the buttons.
   GPX, TCX and FIT files work, gzipped or not, and Strava and Garmin export
   zips work as downloaded (including Garmin's zips inside zips). Strava's
   `activities.csv` is used to tell runs from rides.
2. **Filter**: runs, rides, walks or everything; a date range; and "hide
   start & finish" to blur out where they live.
3. **Style the map**: street map, line colour and glow.
4. **Make a poster**: move the map to frame it (the dashed rectangle is the
   poster), pick paper size, colours and quality, then download a PNG.
   Poster sizes run from A4 to A1 and 24 × 36 in, at up to 300 dpi.

## Street maps and API keys

Since 23 September 2026, CARTO's street maps need a free API key; without
one, every map tile just says "API KEY REQUIRED". Get a key at
[carto.com/basemaps/apikey](https://carto.com/basemaps/apikey/).

**Put your key in `config.js`** (next to `index.html`) and every visitor
gets the map without needing a key of their own:

```js
mapKeys: {
  carto: "paste-your-carto-key-here",
  stadia: "",
  maptiler: "",
},
```

Save the file and reload the page. Visitors can still paste their own key
in step 3; theirs is saved in their own browser only and takes priority.
Like any map key, it's visible to anyone who looks at the page's files. If
your CARTO account lets you restrict a key to your website's address, do
that.

| Map | Key |
|---|---|
| CARTO dark / light *(default)* | Free key from [carto.com](https://carto.com/basemaps/apikey/). |
| Stadia dark | None on `localhost`. On a public site, add your domain (or get a key) at [stadiamaps.com](https://stadiamaps.com). |
| MapTiler dark | Free key from [maptiler.com](https://cloud.maptiler.com/account/keys/). |
| None | No street map: just the glowing runs on black. |

## Browser notes

- Works in current Chrome, Edge, Firefox and Safari.
- Big posters use a lot of memory. A2 at 300 dpi is about 35 million
  pixels; on phones and iPads, Safari may refuse sizes that large, and the
  page will ask for a lower quality.
- Very large exports (several GB) are best added as an unzipped folder.
