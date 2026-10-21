import subprocess
import sys

import voicegateway


def test_import_loads_no_livekit_module() -> None:
    code = (
        "import sys, voicegateway\n"
        "print(','.join(m for m in sys.modules if m == 'livekit' or m.startswith('livekit.')))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


def test_version_matches_the_package_metadata() -> None:
    from importlib.metadata import version

    assert voicegateway.__version__ == version("voicegateway") == "1.0.0rc6"
