#!/usr/bin/env bash
# R1: vendor source must be readable locally before any wiring work.
set -e
mkdir -p vendor && cd vendor
for r in marketcalls/openalgo TauricResearch/TradingAgents Ichinga-Samuel/aiomql \
         666ghj/MiroFish xbtlin/ai-berkshire stefan-jansen/machine-learning-for-trading \
         Zhihan1996/TradeTheEvent; do
  d=$(basename "$r"); [ -d "$d" ] || git clone --depth 1 "https://github.com/$r"
done
