"""Halifax Peninsula coverage test — run a curated set of addresses
through the MCP retrieval service and report what each schedule returns.

Not a unit test — a one-shot QA exercise. Lives under scripts/ rather than
tests/ so pytest doesn't pick it up.
"""
from __future__ import annotations

from dataclasses import dataclass

from layer1.db.session import session_scope
from bylaw_retrieval.retrieval import (
    LocationSlot,
    RetrievalRequest,
    RetrievalService,
    latest_document_id_resolver,
)
from layer2.retrieval.geocode import resolve_location
from layer2.retrieval.location import LocationReference


@dataclass
class TestAddress:
    label: str
    civic_number: str
    street: str
    district: str
    predicted_zone_family: str  # broad category we expect
    predicted_heritage: bool
    predicted_far: bool
    predicted_shadow: bool


CASES = [
    TestAddress("5648 Bilby Street", "5648", "Bilby Street",
                "North End", "residential", False, False, False),
    TestAddress("923 Robie Street (Saint Mary's University)", "923", "Robie Street",
                "South End", "institutional", False, False, False),
    TestAddress("6299 South Street (Dalhousie main campus)", "6299", "South Street",
                "South End", "institutional/UC", False, False, False),
    TestAddress("1726 Hollis Street (Province House)", "1726", "Hollis Street",
                "Downtown", "downtown", True, True, True),
    TestAddress("1650 Argyle Street (Halifax Convention Centre)", "1650", "Argyle Street",
                "Downtown core", "downtown", False, True, True),
    TestAddress("5440 Spring Garden Road (Halifax Central Library)", "5440", "Spring Garden Road",
                "Spring Garden", "centre/downtown", False, True, True),
    TestAddress("5665 Spring Garden Road (Public Gardens)", "5665", "Spring Garden Road",
                "Spring Garden", "park/special", False, False, False),
    TestAddress("1465 Brenton Street (Schmidtville)", "1465", "Brenton Street",
                "Schmidtville", "established residential heritage", True, False, False),
    TestAddress("6080 Quinpool Road", "6080", "Quinpool Road",
                "West End / Quinpool", "centre / corridor", False, False, False),
    TestAddress("2700 Gottingen Street", "2700", "Gottingen Street",
                "North End commercial", "corridor / mid-rise", False, False, False),
    TestAddress("5425 Sackville Street (Halifax Citadel)", "5425", "Sackville Street",
                "Citadel", "park / institutional", False, False, False),
    TestAddress("1894 Barrington Street", "1894", "Barrington Street",
                "Downtown south", "downtown", True, True, True),
]


# Bylaw's enumerated zones from Part II of RCLUB.
PLAUSIBLE_ZONES = {
    "DD", "DH", "CEN-1", "CEN-2", "COR",
    "HR-1", "HR-2", "ER-1", "ER-2", "ER-3", "CH-1", "CH-2",
    "INS", "UC-1", "UC-2", "CLI", "LI", "HRI", "DND",
    "H", "PCF", "RPK", "WA", "HCD-SV",
}


def run() -> None:
    with session_scope() as session:
        service = RetrievalService(
            session, default_document_id_resolver=latest_document_id_resolver
        )
        for case in CASES:
            ref = LocationReference(
                raw_text=case.label,
                kind="civic_address",
                civic_number=case.civic_number,
                street=case.street,
            )
            resolved = resolve_location(session, ref)
            geocoder_status = "MISS"
            geom = None
            if resolved is not None:
                geocoder_status = f"{resolved.source} conf={resolved.confidence:.2f}"
                geom = resolved.geometry

            response = service.search(RetrievalRequest(
                query="zone height FAR heritage setback shadow",
                location=LocationSlot(
                    civic_number=case.civic_number, street=case.street
                ),
                limit=15,
            ))

            # Pull the canonical attributes out of every linked-dataset
            # match across the whole response.
            schedule_results: dict[str, dict] = {}
            for m in response.matches:
                for d in m.linked_datasets:
                    if not d.feature_matches:
                        continue
                    fm = d.feature_matches[0]
                    schedule_results[d.name] = dict(fm.canonical_attributes)

            print("=" * 100)
            print(f"{case.label}")
            print(f"  district={case.district}  predicted={case.predicted_zone_family}")
            print(f"  geocoder: {geocoder_status}")
            if geom:
                lon, lat = geom.get("coordinates", [None, None])
                print(f"  point: lat={lat}, lon={lon}")

            for ds_name in (
                "halifax_zoning_boundaries",
                "halifax_height_precincts",
                "halifax_far_precincts",
                "halifax_heritage_districts",
                "halifax_bonus_zoning_districts",
                "halifax_shadow_impact_areas",
            ):
                attrs = schedule_results.get(ds_name)
                short = ds_name.replace("halifax_", "").replace("_", " ")
                if attrs is None:
                    print(f"    {short:30}  -")
                else:
                    bits = []
                    for k in (
                        "zone_code", "max_height_m", "max_height_storeys",
                        "max_far", "district_name", "district_label",
                        "district_status", "district_code", "impact_area",
                        "bylaw_area_id",
                    ):
                        if attrs.get(k) is not None:
                            bits.append(f"{k}={attrs[k]!r}")
                    print(f"    {short:30}  {' | '.join(bits)}")

            # Apply checks
            verdict_bits: list[str] = []
            zone_attrs = schedule_results.get("halifax_zoning_boundaries")
            if zone_attrs is None:
                verdict_bits.append("FAIL[A]: no zone match")
            else:
                z = zone_attrs.get("zone_code")
                if z not in PLAUSIBLE_ZONES:
                    verdict_bits.append(f"FAIL[B]: zone {z!r} not in bylaw enum")

            height_attrs = schedule_results.get("halifax_height_precincts")
            if height_attrs:
                h_m = height_attrs.get("max_height_m")
                h_st = height_attrs.get("max_height_storeys")
                if h_m is not None and not (8 <= float(h_m) <= 145):
                    verdict_bits.append(f"FAIL[B]: height {h_m}m out of range")
                if h_st is not None and not (3 <= int(h_st) <= 40):
                    verdict_bits.append(f"FAIL[B]: storeys {h_st} out of range")
                if h_m is not None and h_st is not None:
                    verdict_bits.append("FAIL[A]: both height_m AND storeys set (mutual-exclusion violated)")

            heritage_attrs = schedule_results.get("halifax_heritage_districts")
            far_attrs = schedule_results.get("halifax_far_precincts")
            shadow_attrs = schedule_results.get("halifax_shadow_impact_areas")

            # Negative-space checks (Layer D)
            if not case.predicted_heritage and heritage_attrs is not None:
                verdict_bits.append(
                    f"FAIL[D]: unexpected heritage match {heritage_attrs.get('district_name')!r}"
                )
            if case.predicted_heritage and heritage_attrs is None:
                verdict_bits.append("WARN[D]: predicted heritage match but none returned")
            if not case.predicted_far and far_attrs is not None:
                verdict_bits.append(
                    f"FAIL[D]: unexpected FAR match {far_attrs.get('max_far')}"
                )
            if not case.predicted_shadow and shadow_attrs is not None:
                verdict_bits.append(
                    f"FAIL[D]: unexpected shadow impact match {shadow_attrs.get('impact_area')!r}"
                )

            verdict = "PASS" if not verdict_bits else " ; ".join(verdict_bits)
            print(f"  verdict: {verdict}")


if __name__ == "__main__":
    run()
