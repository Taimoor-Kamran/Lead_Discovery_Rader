"""Domain intelligence (v0.13.0): DNS and RDAP facts about a business's registrable domain.

These answer whether or not the homepage could be read, so a site behind a bot challenge
or one that does not load still has true, citable facts. Two rules govern the package:
an answer that is not certain is `unknown` and produces nothing, and nothing about a person
is ever read, stored or logged.
"""
