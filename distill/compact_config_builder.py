import json
DEFAULT_COMPACT_CONFIG = {
  "low": {"orders":[0,1], "backup":1, "max_extra":3},
  "scarce": {"orders":[2,3], "repair":1, "protected":1},
  "medium": {"orders":[0,1,4], "repair":2, "lns":1},
  "large": {"orders":[0,1,2], "repair":1},
  "high_noise": {"orders":[1,4], "repair":2},
  "small": {"orders":[0], "protected":1},
  "tiny": {"orders":[0], "protected":1}
}
if __name__ == "__main__":
    print(json.dumps(DEFAULT_COMPACT_CONFIG, ensure_ascii=False, indent=2))
