from py2app.build_app import py2app as py2app_command
from setuptools import setup


class JarvisPy2App(py2app_command):
    def finalize_options(self):
        self.distribution.install_requires = []
        super().finalize_options()


APP = ["jarvis_dictation_app.py"]
OPTIONS = {
    "argv_emulation": False,
    "packages": ["jarvis_dictation"],
    "plist": {
        "CFBundleDisplayName": "Jarvis Dictation",
        "CFBundleIdentifier": "com.jensbrehmen.jarvisdictation",
        "CFBundleName": "Jarvis Dictation",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "13.0",
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": (
            "Jarvis Dictation needs microphone access to transcribe speech locally on this Mac."
        ),
    },
}


setup(
    name="Jarvis Dictation",
    version="0.1.0",
    app=APP,
    cmdclass={"py2app": JarvisPy2App},
    options={"py2app": OPTIONS},
)
