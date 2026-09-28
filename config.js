// Site settings. Edit this file on your server; visitors can't see or change it
// (they can read it, like any file the browser loads, so only put map keys here).
//
// Map API keys: paste yours between the quotes and every visitor gets the street
// map without needing a key of their own. Leave a key empty ("") to let visitors
// paste their own in the page.
//   CARTO (free):    https://carto.com/basemaps/apikey/
//   Stadia Maps:     https://client.stadiamaps.com/signup/
//   MapTiler (free): https://cloud.maptiler.com/account/keys/
window.RUN_HEATMAP_CONFIG = {
  mapKeys: {
    carto: "",
    maptiler: "",
  },

  // The street map selected when someone first opens the page:
  // "carto-dark", "carto-light", "stadia-dark", "maptiler-dark" or "none".
  defaultMap: "stadia-dark",
};
