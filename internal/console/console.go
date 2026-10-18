// Package console serves the built dashboard from disk (decision 0011): web's own origin, so the
// console's sign-in and API calls need no CORS (decision 0027).
package console

import (
	"io/fs"
	"net/http"
	"path"
	"strings"
)

// immutable is the cache policy for Vite's content-hashed files under assets/: a new build gives
// them new names, so a browser may keep them for a year.
const immutable = "public, max-age=31536000, immutable"

// Handler serves fsys, the dashboard's dist folder. A file is served as it is; any other path gets
// index.html, so the router can open deep links such as /org/<id>/project/<id>/calls. A missing
// file with an extension (an old asset), a path under /api/ or a method other than GET or HEAD is a
// 404, never the app.
// http.FileServerFS does the serving (ranges, conditional requests, content types); this only adds
// the fallback it lacks, and never lists a directory.
func Handler(fsys fs.FS) http.Handler {
	files := http.FileServerFS(fsys)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			http.NotFound(w, r)
			return
		}
		name := strings.TrimPrefix(path.Clean("/"+r.URL.Path), "/")
		if info, err := fs.Stat(fsys, name); err == nil && !info.IsDir() {
			if strings.HasPrefix(name, "assets/") {
				w.Header().Set("Cache-Control", immutable)
			}
			files.ServeHTTP(w, r)
			return
		}
		if name == "api" || strings.HasPrefix(name, "api/") || path.Ext(name) != "" {
			http.NotFound(w, r)
			return
		}
		// index.html names the current hashed files, so it is revalidated on every load.
		w.Header().Set("Cache-Control", "no-cache")
		http.ServeFileFS(w, r, fsys, "index.html")
	})
}
