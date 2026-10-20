"""The hand-edited configuration still kept in the Google spreadsheet, copied into the database.

Requests never read the spreadsheet: a background job asks Google whether the file changed (a cheap
metadata read) and only then rereads the tabs and applies them. When the Dashboard gets its own admin
pages, only this editing source changes; everything else already reads the database.
"""
