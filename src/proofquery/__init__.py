"""ProofQuery: evidence-locked analytics workbench.

Every number this package reports traces to a row in facts.complaints, which
traces to a byte range in data/raw/cfpb_complaints.csv, which traces to a
CC0 federal dataset pinned by SHA-256 in meta.dataset.
"""

__version__ = "0.1.0"
