import copy
import unittest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from vision_protocol import ProtocolError,make_result,validate_result
from vision_sheets_api import create_sheets_app

FAMILY='fixture-owner-health-family-'+'f'*40

def verify(key):
    if key!=FAMILY:raise HTTPException(401)
def rows():
    return {'members':[{'Line User ID':'owner','狀態':'啟用'},{'Line User ID':'other','狀態':'啟用'}],
            'grants':[{'user_id':user,'status':True,'preview':True,'edit':True} for user in ('owner','other')],
            'devices':[]}
def headers(user='owner',role='member'):
    return {'X-API-Key':FAMILY,'X-Dashboard-User':user,'X-Dashboard-Role':role,'X-Dashboard-Session-Expires':'1100'}
def command():
    return {'protocol':'vision.v1','type':'command','request_id':'r','device_id':'mini','action':'status.get','deadline':1010,'payload':{}}
def health(available=True):
    return {'adapter':'local-health','available':available,'service':{'reachable':available,'app_version':'1.2.3' if available else None,'mode':'localhost-dev' if available else None,'config_schema':2 if available else None},'reason':'http_service_responding' if available else 'local_health_unavailable'}
def result(payload):
    return make_result({**command(),'session_nonce':'s'*32},'ok',payload)

class OwnerTests(unittest.TestCase):
    def test_only_explicit_owner_member_status_and_mask_other_capabilities(self):
        async def reader():return rows()
        app=create_sheets_app(reader,verify,owner_user_id='owner',clock=lambda:1000)
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/vision/v1/access',headers=headers()).json(),{'capabilities':{'status':True,'preview':False,'edit':False}})
            for user,role in [('other','member'),('owner','kid'),('unknown','member')]:
                self.assertEqual(client.get('/api/vision/v1/access',headers=headers(user,role)).status_code,403)
                self.assertEqual(client.post('/api/vision/v1/command',headers=headers(user,role),json=command()).status_code,403)
            self.assertEqual(len(app.state.vision_control_hub._entries),0)
    def test_missing_invalid_pin_and_owner_without_required_grant_fail_closed(self):
        for pin in (None,'','invalid owner','other-not-enabled'):
            async def reader():return rows()
            with TestClient(create_sheets_app(reader,verify,owner_user_id=pin,clock=lambda:1000)) as client:
                self.assertEqual(client.get('/api/vision/v1/access',headers=headers()).status_code,403)
        for disabled in (True,False):
            async def reader():
                value=rows()
                if disabled:value['members'][0]['狀態']='停用'
                else:value['grants'][0]['status']=False
                return value
            with TestClient(create_sheets_app(reader,verify,owner_user_id='owner',clock=lambda:1000)) as client:
                self.assertEqual(client.get('/api/vision/v1/access',headers=headers()).status_code,403)

class HealthContractTests(unittest.TestCase):
    def test_real_health_available_and_unavailable_strict_variant(self):
        for available in (True,False):
            value=result(health(available))
            self.assertEqual(validate_result(value,'status.get'),value)
    def test_sensitive_extra_invalid_version_schema_and_inconsistent_health_rejected(self):
        changes=[('image','private'),('model','yolo11n'),('camera_url','rtsp://private'),('roi',[])]
        for key,value in changes:
            payload=health();payload[key]=value
            with self.assertRaises(ProtocolError):validate_result(result(payload),'status.get')
        for key,value in [('app_version','https://private'),('app_version','1.2'),('mode','camera-online'),('config_schema',True),('config_schema',101),('reachable',False),('secret','private')]:
            payload=health();payload['service'][key]=value
            with self.assertRaises(ProtocolError):validate_result(result(payload),'status.get')
        payload=health(False);payload['service']['app_version']='1.2.3'
        with self.assertRaises(ProtocolError):validate_result(result(payload),'status.get')
        payload=health();payload['reason']='model_ready'
        with self.assertRaises(ProtocolError):validate_result(result(payload),'status.get')
    def test_synthetic_fixture_variant_remains_explicitly_synthetic(self):
        payload={'adapter':'synthetic','available':True,'config_revision':0,'detector_revision':0,'detector':{'model':'yolo11n','precision':'fp16'},'zone_count':0}
        self.assertEqual(validate_result(result(payload),'status.get')['payload']['adapter'],'synthetic')
if __name__=='__main__':unittest.main()
