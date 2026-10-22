LMD Fixer
=========

Cleans up laser metal deposition (LMD) G-code / PTP programs before they're
run on the machine. You review every change before it's made, on a map of
the bed, in 3D and in the program itself.


Starting it
-----------
1. Unzip the whole folder somewhere you can write to, e.g. Documents or the
   Desktop. Don't run it from inside the zip.
2. Double-click "LMD Fixer.exe". The first launch can take a few seconds.

You don't need Python or anything else installed. Everything stays on your
PC: nothing is uploaded anywhere.

If Windows shows "Windows protected your PC", click "More info" and then
"Run anyway". The app isn't code-signed, so Windows doesn't recognise it yet.


Using it
--------
- Open a program with File > Open, or drag it onto the window.
- Switch the fixes you want on or off in the list on the left.
- Review each step, then press Apply step. Back undoes a step.
- At the end, check the side-by-side comparison and press Save.

Help > Keyboard and mouse lists every shortcut. The Dark / Light / Vivid
buttons at the top right change the look. A 3Dconnexion SpaceMouse moves the
plan and 3D views.

Close the window to quit.


Choosing which fixes are offered
--------------------------------
Open "fix_settings.toml" (in this folder) in Notepad. Set a fix to false to
hide it, or true to show it, then save and restart the app.


The material database
---------------------
Tools > Material database records what each recipe number (the P in
M98 Pxxxx) sets on the control panel. It's saved as recipes.csv in
%APPDATA%\LMD-Fixer, not in this folder, so unzipping a newer version of the
app keeps it. You can open it in Excel too, but close it there before saving
from the app. To keep it somewhere else (e.g. a shared OneDrive folder), set
the path under [database] in fix_settings.toml.


Something went wrong?
---------------------
Errors are shown in a message and saved to
%APPDATA%\LMD-Fixer\logs\lmd-fixer.log. Send that file, with the version
number (shown at the bottom left of the window, or in the zip's name), to
whoever gave you the app.
