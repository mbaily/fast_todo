Write into README.md the following points or explanations:

explain server usage on debian and windows

Windows You can use it on windows as a local app (or access it while running from another device). Linux You can set it up as a server on a mini PC using a linux distro and using your internet router's DMZ zone or IP forwarding facilities. Then you can use it via your device's web browser (smartphone, tablet, laptop or desktop). Tested on iOS safari and android Google Chrome a bit. Tested on debian 13 (server and Chrome browser) and windows 11 (server and Chrome browser). Use the powershell script on windows. It should create a python .venv and install the necessary packages.

Explain self-signed certs

explain SECRET_KEY env var and env files under debian and then find out how to do it in windows 11
On windows the powershell script should setup an env file for SECRET_KEY, in gpt5_fast_todo.env containing a new SECRET_KEY. This is for the JWT security access token the client stores (received from server). On a more permanent server on Linux, set up the secret key env file in /etc/....., rotate it if you want (or not if you can't be bothered). When you rotate the SECRET_KEY, you may have to logout and log back in again.

explain hash tags (especially the bit about you can type them into a list name or todo name and they get extracted and added)

explain scripts run server scripts for windows and debian
it should create a venv in .venv and automatically installed packages using pip then run the server

explain scripts/add_user - admin is manual command-line from local PC or linux server (tested on Debian 13)

Explain auto-save in todo text and notes in todo.html (note it does not auto-add)

Explain recursive sublists and todos, sublist of todos, sublists of lists

Explain prioritie numbers in index.html and red numbers in index.html

