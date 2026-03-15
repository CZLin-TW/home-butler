import panasonic_api

panasonic_api.PANASONIC_ACCOUNT = "chuanzonglin@gmail.com"
panasonic_api.PANASONIC_PASSWORD = "czlin86pana"
panasonic_api.login()
status = panasonic_api.get_dehumidifier_status('2C9FFB636189BEF8C43F326A9E580CB3314D3E7B884B', '2C9FFB636189')
print(status)
print(panasonic_api.format_dehumidifier_status(status, '除濕機-1'))