# jkview

Many thanks to the Code Alliance for the JK Specs hosted at https://www.massassi.net/jkspecs/
Without their work of documenting JK file formats, this viewer would not have been possible.

## Quick getting started instructions

After you have checked out the repository, copy the Jedi Knight game files with the extension `GOB`
into the root directory of the repository: `JK1.GOB`, `JK1CTF.GOB`, `JK1MP.GOB`, `Res1hi.gob`, `Res2.gob`.
For Mysteries of the Sith the extension is `goo` and the game files named `JKMRES.GOO` and `JKMsndLO.goo`;
note that these need to be added in addition to the JK files.

If you don't own the game, you can download the demo from
https://archive.org/details/StarWarsJediKnightDarkForcesII_1020 (it comes as an installer for Windows).

Then, initialize a local Python environment using `pipenv install`. Note that `pipenv` needs to be
available on your system. In case you run into any problems with the install command, try
`pipenv update` to update dependencies; they hopefully then install successfully. Next, switch your
shell into the environment using `pipenv shell`.

Before starting the server that allows you to view JK maps in your browser, you need to configure
it: Make a copy of `config.py-default` and name it `config.py`. Comments in the file explain the
values you can set there.

Finally, start the server using the script `start.sh`. Then navigate using a web browser to, for
example, `http://127.0.0.1:5000/level/?url=https://www.massassi.net/media/levels/files/jkmp/baronnight.zip`
to view a level or `http://127.0.0.1:8080/skins/?url=https://www.massassi.net/media/levels/files/jkmod/clones.zip`
to view skins.
The `url` in the address is expected to be a ZIP archive of a map as hosted on the Massassi Temple.
See https://www.massassi.net/levels/ for all the hosted goodness there.
