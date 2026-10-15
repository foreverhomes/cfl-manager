"""Fantrax fantasy hockey league store + category analysis."""

import warnings

# macOS system Python links LibreSSL; urllib3 v2 warns about it on import but works fine.
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")
