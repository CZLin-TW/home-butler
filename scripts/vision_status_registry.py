"""Offline operator enrollment. Never takes a token in argv or prints credentials."""
import argparse
import os
from pathlib import Path
import stat
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from vision_pilot import initialize_registry,enroll,revoke

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    for action in ('init','enroll','revoke'):
        command=sub.add_parser(action)
        command.add_argument('--db',required=True)
        if action != 'init':
            command.add_argument('--record-id',required=True)
        if action == 'enroll':
            command.add_argument('--kind',choices=['service','device'],required=True)
            command.add_argument('--device-id',required=True)
            command.add_argument('--expires-at',type=float,required=True)
            source=command.add_mutually_exclusive_group(required=True)
            source.add_argument('--secret-file')
            source.add_argument('--stdin',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.action == 'init':
            initialize_registry(args.db)
        elif args.action == 'revoke':
            revoke(args.db,args.record_id)
        else:
            if args.secret_file:
                fd=os.open(args.secret_file,os.O_RDONLY|os.O_NOFOLLOW)
                with os.fdopen(fd,'rb') as file:
                    info=os.fstat(file.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode)&0o077 or info.st_nlink != 1 or not 32 <= info.st_size <= 514:
                        raise ValueError()
                    raw=file.read(514)
            else:
                if sys.stdin.isatty():
                    raise ValueError()  # Avoid terminal echo; require a pipe or file redirection.
                raw=sys.stdin.buffer.read(514)
            token=raw.decode('ascii').removesuffix('\n').removesuffix('\r')
            enroll(args.db,record_id=args.record_id,token=token,kind=args.kind,device_id=args.device_id,expires_at=args.expires_at)
        print('registry operation completed')
        return 0
    except Exception:
        print('registry operation denied',file=sys.stderr)
        return 1
if __name__=='__main__':
    raise SystemExit(main())
