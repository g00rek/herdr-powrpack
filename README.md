# Powr Pack

Extras for the Herdr desktop app, each one a mod of its own: install the one
you want, leave the rest.

Written from scratch against Herdr's own mod interface. No part of Herdr's
shipped code is copied or redistributed here.

## powr-spaces

The list of projects, with things Herdr does not know about on each row.

A Herdr panel draws all of its own content and nothing can be handed to it
from outside, so a mark on a project's row means owning the list. That is
what this is.

What it shows, per project: its colour and initial, its name, the branch when
it is not the trunk, a dot when an agent in it wants you — and a browser mark
that is lit when that project has a group of tabs of its own. Pressing the
mark gives a project a browser or takes it away; it writes the same setting
Browsr's own panel does, so the two can never disagree.

Folders come first and closed, the projects in none of them below. Clicking a
project switches to it; right-clicking opens Herdr's own project menu, because
renaming and closing are its business.

Folders are read from Herdr's list once and then kept separately, so nothing
here can damage the list you can switch back to.

Not yet: making, renaming and deleting folders, dragging to reorder, the
machine sections.

## browsr

A browser group per project: <https://github.com/g00rek/browsr>

## Licence

[MIT](LICENSE)
