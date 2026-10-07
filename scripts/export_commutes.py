from __future__ import annotations

import json

from travelsmart.commute_export import export_commutes


if __name__ == "__main__":
    print(json.dumps(export_commutes()))
