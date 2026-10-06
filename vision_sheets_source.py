"""Read-only source adapter; constructed only by explicitly enabled installer.
No household wrapper retries/cache. One batch for member IDs, grants, device digests.
"""
import asyncio
import json
import re

def _rows(values):
    if not isinstance(values,list) or not values or len(values)>257:
        raise ValueError('invalid_sheet')
    headers=values[0]
    if not all(isinstance(h,str) for h in headers) or len(headers)!=len(set(headers)):
        raise ValueError('invalid_headers')
    if any(not isinstance(row,list) or len(row)>len(headers) for row in values[1:]):
        raise ValueError('invalid_row')
    return [dict(zip(headers,row+['']*(len(headers)-len(row)))) for row in values[1:] if row]

def _bool(value):
    if value not in ('TRUE','FALSE'):
        raise ValueError('invalid_boolean')
    return value=='TRUE'

class ProductionSheetsReader:
    def __init__(self):
        from config import GOOGLE_CREDENTIALS,SPREADSHEET_ID
        from google.oauth2.service_account import Credentials
        from google.auth.transport.requests import AuthorizedSession
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',SPREADSHEET_ID):
            raise ValueError('invalid_sheet_id')
        credentials=Credentials.from_service_account_info(json.loads(GOOGLE_CREDENTIALS),scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'])
        self.session=AuthorizedSession(credentials,max_refresh_attempts=0,refresh_timeout=3)
        self.url='https://sheets.googleapis.com/v4/spreadsheets/'+SPREADSHEET_ID+'/values:batchGet'
    def read(self):
        response=self.session.get(self.url,params=[('ranges',"'家庭成員'"),('ranges',"'Vision Grants'"),('ranges',"'Vision Devices'")],timeout=(2,3),allow_redirects=False)
        if response.status_code!=200 or len(response.content)>1048576:
            raise ValueError('reader_failed')
        ranges=response.json()['valueRanges']
        if len(ranges)!=3:
            raise ValueError('reader_failed')
        if set(ranges[1]['values'][0]) != {'user_id','status','preview','edit'} or set(ranges[2]['values'][0]) != {'record_id','digest','device_id','scopes','expires_at','revoked'}:
            raise ValueError('invalid_registry_headers')
        members,grants,devices=map(lambda r:_rows(r['values']),ranges)
        return {'members':[{'Line User ID':r['Line User ID'],'狀態':r['狀態']} for r in members],
                'grants':[{'user_id':r['user_id'],**{c:_bool(r[c]) for c in ('status','preview','edit')}} for r in grants],
                'devices':[{'record_id':r['record_id'],'digest':r['digest'],'device_id':r['device_id'],'scopes':r['scopes'].split(','),'expires_at':float(r['expires_at']),'revoked':_bool(r['revoked'])} for r in devices]}
    async def __call__(self):
        return await asyncio.to_thread(self.read)
