#!/usr/bin/env python3
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
loader = unittest.TestLoader()
suite = unittest.TestSuite()
suite.addTests(loader.discover(os.path.join(ROOT, 'tests'), pattern='test_*.py'))
runner = unittest.TextTestRunner(verbosity=2)
result = runner.run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
