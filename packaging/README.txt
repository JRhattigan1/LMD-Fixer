LMD Fixer
=========

Cleans up laser metal deposition (LMD) G-code / PTP programs before they're
run on the machine. You review every change before it's made.


Starting it
-----------
1. Unzip the whole folder somewhere you can write to, e.g. Documents or the
   Desktop. Don't run it from inside the zip.
2. Double-click "LMD Fixer.exe".
3. A black console window opens, and after a few seconds the app opens in
   your web browser. The first launch can take up to about 20 seconds.

You don't need Python or anything else installed. Everything stays on your
PC: the app only accepts connections from this computer, and your files are
never uploaded anywhere.

If Windows shows "Windows protected your PC", click "More info" and then
"Run anyway". The app isn't code-signed, so Windows doesn't recognise it yet.


Stopping it
-----------
Close the black console window. Closing the browser tab on its own leaves the
app running in the background.

If the browser tab is closed by accident, open the address shown in the
console window again (usually http://127.0.0.1:8501).


Choosing which fixes are offered
--------------------------------
Open "fix_settings.toml" (in this folder) in Notepad. Set a fix to false to
hide it, or true to show it, then save and refresh the browser tab.


Something went wrong?
---------------------
If the app won't start, the console window stays open and shows the error.
Send a screenshot of it, with the version number from the app's sidebar
(or the folder name of the zip), to whoever gave you the app.
