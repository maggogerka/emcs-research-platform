# PyInstaller specification for the Windows desktop application.

hiddenimports = [
    "pyqtgraph.exporters",
    "pyqtgraph.exporters.ImageExporter",
    "pyqtgraph.exporters.SVGExporter",
    "serial.tools.list_ports",
    "analysis.analyze_session",
    "analysis.metrics",
    "analysis.reporting",
    "matplotlib.backends.backend_svg",
]

a = Analysis(
    ["desktop_app/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="EMCSResearchPlatform",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
