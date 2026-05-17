.PHONY: build up up-detach down logs verify check-search check-tile save-tile clean

build:
	docker compose build

up:
	docker compose up

up-detach:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f hotosm-imagery-tile

verify:
	bash scripts/verify.sh

# Search a small bbox around Saarland (the demo item bbox from the
# HOTOSM STAC, near Trier, Germany).
check-search:
	curl -s 'http://localhost:8080/search?bbox=7.68,49.56,7.70,49.57&limit=3' \
	  | python3 -m json.tool

# Fetch metadata for whatever the search returned first.
check-first-item:
	@id=$$(curl -s 'http://localhost:8080/search?bbox=7.68,49.56,7.70,49.57&limit=1' \
	    | python3 -c 'import json,sys; print(json.load(sys.stdin)["items"][0]["id"])'); \
	  echo "item: $$id"; \
	  curl -s "http://localhost:8080/items/$$id/metadata" | python3 -m json.tool

save-tile:
	@mkdir -p /tmp/hotosm-tiles
	curl -so /tmp/hotosm-tiles/test.png 'http://localhost:8080/tiles/17/68223/45102.png'
	@echo "Saved to /tmp/hotosm-tiles/test.png ($$(wc -c < /tmp/hotosm-tiles/test.png) bytes)"

clean:
	docker compose down --rmi local
