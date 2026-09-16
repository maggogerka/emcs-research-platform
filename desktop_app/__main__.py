import sys

if len(sys.argv) > 1 and sys.argv[1] == "--analyze":
    from analysis.analyze_session import main

    sys.argv = [sys.argv[0], *sys.argv[2:]]
else:
    from .main import main

raise SystemExit(main())
