#!/usr/bin/env sh

bkk import --format krp --by-section --out /home/chris/00scratch/bkk-work/output
bkk index merge /home/chris/00scratch/bkk-work/output
