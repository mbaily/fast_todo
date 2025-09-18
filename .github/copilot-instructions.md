# GitHub Copilot Instructions

You are free to use python, uv, uv pip and pytest commands. Also node, npx, npm.
You're not allowed to use bash heredocs (using <<), or UNIX commmands, or use bash pipes.
No UNIX commands are allowed like find, grep, sed, awk, ls, cat, echo, sqlite3, etc.
You are allowed to create as many of your own python scripts as you need (100's if you need), refine them, and run them using the python command. You can create any script file you like in the scripts/ directory and run it using the python command. They script files can be as large as you like. You can keep adding to them, refine them, and change the main function call to run a different function in the script. It is recommended to use the python argparse python module in the script to allow different commands or arguments to be run in your refined script files. Try to refine the script files you create to suit current and future needs of the workspace. Not just throwaway once-only scripts. Don't hestiate to build a refined library of scripts usefil for this workspace in the scripts/ folder.

Always separate out javascript code for a browser to run into a separate .js file. Never inline more than a few lines of javascript code in a .html file.

Note we are using uv or uv pip and not pip or pip3. You are not allowed to use pip, only uv or uv pip.

When installing python packages with uv pip or uv, do not specify older versions of packages in requirements.txt. Always install the latest version of a package.

Always activate the virtual environment use `source .venv/bin/activate` before running uv, uv pip, pytest or python commands.

In HTML web apps, don't write any HTML forms at all. Always write a server JSON endpoint and wire to JS in the client.
