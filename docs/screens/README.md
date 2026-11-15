# Console screens

One picture of every console page, at desktop (1440 px) and phone (390 px) width, light theme. Each is drawn from the real dashboard code on the mock API's sample data (`dashboard/src/mocks`), not from a live install. `screens.json` lists each page with its route, sidebar group, the file that draws it and its two pictures.

Look here before proposing or building UI, so a new idea starts from what already exists.

Regenerate after any UI change:

```sh
make dashboard-screens
```

`SCREENS_DARK=1 make dashboard-screens` also takes dark pictures; they stay local. The page list lives in `dashboard/e2e/pages.ts`, shared with the shell browser checks, which fail when a sidebar link is missing from it.
