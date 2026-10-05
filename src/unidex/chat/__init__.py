"""The chat layer: turns a student's message into a structured query.

The model never produces links or file names. It (or the rules) only fills in
a small form (course, exam, kinds of material, years, topics); the answer then
comes from the catalog like any other search.
"""
