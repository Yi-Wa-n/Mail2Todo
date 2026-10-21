"""Optional new-project task entry. No task is installed automatically."""
import json
import sys
from backend.storage.vault import HOME
from backend.mail.core import now

with (HOME/'local-gate.log').open('a',encoding='utf-8') as log:
    sys.stdout=sys.stderr=log
    print(json.dumps({'scheduled_start':True,'at':now().isoformat(),'local_only':True}),flush=True)
    from backend.jobs.manual_scan import main
    main()
