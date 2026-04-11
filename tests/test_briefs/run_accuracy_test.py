#!/usr/bin/env python3
"""Run CiteVerify accuracy tests against test briefs.

Usage:
    cd /Users/rick/Projects/citeverify
    source venv/bin/activate
    python tests/test_briefs/run_accuracy_test.py [--brief a|b|c|d|e|f|g|h|i|j|k|l|all]

Each brief tests different aspects:
  Brief A: All real cases, accurate quotes (summary judgment) — expect all VERIFIED
  Brief B: Real cases, fabricated quotes (pleading/negligence) — expect quotes FLAGGED
  Brief C: Mix of real + hallucinated cases (med mal) — expect fake cases caught
  Brief D: All real cases, accurate quotes (employment discrimination) — expect all VERIFIED
  Brief E: Real cases, fabricated quotes (criminal procedure) — expect quotes FLAGGED
  Brief F: Mix of real + hallucinated cases (IP/class actions) — expect fake cases caught
  Brief G: STRESS TEST — Constitutional Law (18 cites: real, fake, fabricated)
  Brief H: STRESS TEST — Securities Fraud (16 cites: abbreviation-heavy names)
  Brief I: STRESS TEST — Admin Law / Fed Procedure (16 cites: Mfrs., Ass'n, Pharm., etc.)
  Brief J: EDGE CASES — Per curiam, In re, string cites, signal prefixes, old/new cases
  Brief K: SUBTLE TRAPS — Wrong volume, dissent-as-majority, holding swaps, wrong year
  Brief L: LOOKALIKES — Near-miss names, party reversals, wrong court, In re off-by-one
"""

import argparse
import json
import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.pipeline import run_verification

BRIEF_DIR = os.path.dirname(os.path.abspath(__file__))

BRIEFS = {
    "a": {
        "file": "brief_a_all_real.docx",
        "description": "All real cases with accurate quotes",
        "expectations": {
            "All citations should be found in CourtListener": True,
            "Quotes should match source text": True,
            "Characterizations should be accurate": True,
            "Expected statuses: mostly verified": True,
        },
    },
    "b": {
        "file": "brief_b_fabricated_quotes.docx",
        "description": "Real cases with FABRICATED quotes and altered characterizations",
        "expectations": {
            "Citations should be found (cases are real)": True,
            "Twombly quote should be flagged (says 'beyond reasonable doubt' — wrong)": True,
            "Iqbal quote should be flagged ('automatic inference' is fabricated)": True,
            "Palsgraf characterization is WRONG (not about 'absolute duty')": True,
            "Miranda characterization broadened ('all government interactions')": True,
            "East River quote is altered": True,
            "Saratoga Fishing is correctly characterized (control case)": True,
        },
    },
    "c": {
        "file": "brief_c_mixed_real_fake.docx",
        "description": "Mix of REAL and HALLUCINATED cases",
        "expectations": {
            "REAL - Daubert v. Merrell Dow, 509 U.S. 579": "verified",
            "FAKE - Whitfield v. Pacific Medical, 387 F.3d 1042": "error/unverifiable",
            "REAL - Kumho Tire v. Carmichael, 526 U.S. 137": "verified",
            "FAKE - Morrison v. St. Luke's, 498 F.3d 835": "error/unverifiable",
            "REAL - Darling v. Charleston, 33 Ill. 2d 326": "verified/unverifiable",
            "FAKE - Rodriguez v. Northwestern Memorial, 612 F.3d 544": "error/unverifiable",
            "REAL - Burrage v. United States, 571 U.S. 204": "verified",
            "FAKE - Kellerman v. Providence Health, 348 Or 456": "error/unverifiable",
            "FAKE - Chen v. Legacy Health, 285 Or App 312": "error/unverifiable",
            "REAL - BMW v. Gore, 517 U.S. 559": "verified",
            "REAL - State Farm v. Campbell, 538 U.S. 408 (fabricated ratio)": "warning/error",
        },
    },
    "d": {
        "file": "brief_d_employment_real.docx",
        "description": "All real cases — Employment Discrimination / Title VII",
        "expectations": {
            "All citations should be found (landmark Title VII cases)": True,
            "McDonnell Douglas v. Green — burden-shifting framework": "verified",
            "Burdine — burden of production vs persuasion": "verified",
            "Griggs v. Duke Power — disparate impact": "verified",
            "Ricci v. DeStefano — strong basis in evidence": "verified",
            "Burlington Northern v. White — retaliation standard": "verified",
            "Meritor Savings Bank v. Vinson — hostile environment": "verified",
            "Harris v. Forklift — totality of circumstances": "verified",
        },
    },
    "e": {
        "file": "brief_e_criminal_fabricated.docx",
        "description": "Real cases with FABRICATED quotes — Criminal Procedure / 4th Amendment",
        "expectations": {
            "Citations should be found (cases are real)": True,
            "Mapp v. Ohio — fabricated 'automatically inadmissible' quote": "warning/error",
            "Katz v. United States — fabricated 'absolute protection' quote": "warning/error",
            "Terry v. Ohio — 'comprehensive search' is wrong (only limited pat-down)": "warning/error",
            "Illinois v. Gates — 'direct personal knowledge' is wrong (totality test)": "warning/error",
            "Riley v. California — CORRECT characterization (control)": "verified",
            "United States v. Leon — 'subjectively believes' is wrong (objective test)": "warning/error",
        },
    },
    "f": {
        "file": "brief_f_ip_mixed.docx",
        "description": "Mix of REAL and HALLUCINATED cases — IP & Class Actions",
        "expectations": {
            "REAL - Alice Corp. v. CLS Bank, 573 U.S. 208": "verified",
            "FAKE - Sterling Technologies v. DataVault, 789 F.3d 432": "error/unverifiable",
            "REAL - Campbell v. Acuff-Rose Music, 510 U.S. 569": "verified",
            "FAKE - Henderson v. Creative Digital Solutions, 634 F.3d 891": "error/unverifiable",
            "REAL - eBay v. MercExchange, 547 U.S. 388": "verified",
            "FAKE - Marcus v. Silicon Valley Innovations, 856 F.3d 1124": "error/unverifiable",
            "REAL - Wal-Mart v. Dukes, 564 U.S. 338": "verified",
            "FAKE - Porter v. National Financial Services, 912 F.3d 267": "error/unverifiable",
            "REAL - AT&T Mobility v. Concepcion, 563 U.S. 333 (mischaracterized)": "warning/error",
        },
    },
    "g": {
        "file": "brief_g_constitutional_stress.docx",
        "description": "STRESS TEST — Constitutional Law / Civil Rights (18 citations)",
        "expectations": {
            "REAL - Marbury v. Madison, 5 U.S. 137": "verified",
            "FAKE - Patterson v. State Bd. of Educ., 543 F.3d 891": "error/unverifiable",
            "REAL - Brown v. Board of Education, 347 U.S. 483": "verified",
            "REAL - Loving v. Virginia, 388 U.S. 1": "verified",
            "REAL - Obergefell v. Hodges, 576 U.S. 644": "verified",
            "FAKE - Davidson v. Metro. Housing Auth., 723 F.3d 556": "error/unverifiable",
            "REAL - New York Times v. Sullivan, 376 U.S. 254": "verified",
            "REAL - Tinker v. Des Moines, 393 U.S. 503": "verified",
            "REAL - Brandenburg v. Ohio, 395 U.S. 444": "verified",
            "REAL - Citizens United v. FEC, 558 U.S. 310": "verified",
            "FAKE - Whitmore v. City of Portland, 678 F.3d 1205": "error/unverifiable",
            "FAKE - Reynolds v. Fed. Election Comm'n, 891 F.3d 445": "error/unverifiable",
            "REAL - Reed v. Town of Gilbert, 576 U.S. 155 (fabricated char)": "warning/error",
            "REAL - Gideon v. Wainwright, 372 U.S. 335": "verified",
            "REAL - District of Columbia v. Heller, 554 U.S. 570": "verified",
            "REAL - Shelby County v. Holder, 570 U.S. 529 (fabricated quote)": "warning/error",
            "REAL - McDonald v. Chicago, 561 U.S. 742 (fabricated char)": "warning/error",
            "FAKE - Crawford v. State Dep't of Educ., 456 F.3d 789": "error/unverifiable",
        },
    },
    "h": {
        "file": "brief_h_securities_stress.docx",
        "description": "STRESS TEST — Commercial / Securities Fraud (16 citations)",
        "expectations": {
            "REAL - International Shoe v. Washington, 326 U.S. 310": "verified",
            "REAL - Erie R.R. Co. v. Tompkins, 304 U.S. 64": "verified",
            "REAL - Shady Grove v. Allstate, 559 U.S. 393": "verified",
            "FAKE - Thornton v. Pacific Coast Fin. Group, 823 F.3d 1156": "error/unverifiable",
            "FAKE - Westbrook Inv. Corp. v. Atlantic Mgmt., 567 F. Supp. 2d 432": "error/unverifiable",
            "REAL - Basic Inc. v. Levinson, 485 U.S. 224": "verified",
            "REAL - Tellabs v. Makor Issues & Rights, 551 U.S. 308": "verified",
            "REAL - Stoneridge v. Scientific-Atlanta, 552 U.S. 148": "verified",
            "REAL - Dura Pharms. v. Broudo, 544 U.S. 336": "verified",
            "REAL - Halliburton v. Erica P. John Fund (fabricated)": "warning/error",
            "REAL - Morrison v. Nat'l Australia Bank (fabricated char)": "warning/error",
            "FAKE - Meridian Capital v. Blackstone Advisors, 745 F.3d 328": "error/unverifiable",
            "FAKE - Sullivan v. Global Tech. Innovations, 901 F.3d 178": "error/unverifiable",
            "REAL - Blue Chip Stamps v. Manor Drug Stores, 421 U.S. 723": "verified",
            "REAL - Janus Capital v. First Derivative (fabricated char)": "warning/error",
            "FAKE - Chen v. Pacific Semiconductor, 678 F.3d 923": "error/unverifiable",
        },
    },
    "i": {
        "file": "brief_i_admin_stress.docx",
        "description": "STRESS TEST — Admin Law / Fed Procedure (16 citations)",
        "expectations": {
            "REAL - Lujan v. Defenders of Wildlife, 504 U.S. 555": "verified",
            "REAL - Spokeo v. Robins, 578 U.S. 330": "verified",
            "FAKE - Cartwright v. Dep't of Health & Human Servs., 634 F.3d 782": "error/unverifiable",
            "REAL - Massachusetts v. EPA, 549 U.S. 497 (fabricated char)": "warning/error",
            "REAL - Chevron v. NRDC, 467 U.S. 837": "verified",
            "REAL - Auer v. Robbins, 519 U.S. 452": "verified",
            "REAL - Motor Vehicle Mfrs. v. State Farm, 463 U.S. 29": "verified",
            "FAKE - Lexington Envtl. Servs. v. EPA, 789 F.3d 1034": "error/unverifiable",
            "REAL - Kisor v. Wilkie, 588 U.S. 558 (fabricated char)": "warning/error",
            "FAKE - Nakamura v. Fed. Trade Comm'n, 845 F.3d 267": "error/unverifiable",
            "REAL - FCC v. Fox Television, 556 U.S. 502 (fabricated quote)": "warning/error",
            "FAKE - Westfield Pharm. Corp. v. FDA, 912 F.3d 456": "error/unverifiable",
            "FAKE - O'Brien v. Nat'l Labor Relations Bd., 778 F.3d 1124": "error/unverifiable",
            "REAL - Bristol-Myers Squibb v. Superior Court, 582 U.S. 255": "verified",
            "REAL - Daimler AG v. Bauman, 571 U.S. 117": "verified",
            "REAL - Piper Aircraft v. Reyno, 454 U.S. 235": "verified",
        },
    },
    "j": {
        "file": "brief_j_edge_cases.docx",
        "description": "EDGE CASES — Per curiam, In re, string cites, signals, old/new cases",
        "expectations": {
            "REAL - Bush v. Gore, 531 U.S. 98 (per curiam)": "verified",
            "REAL - In re Winship, 397 U.S. 358": "verified",
            "REAL - Roe v. Wade, 410 U.S. 113": "verified",
            "REAL - Mathews v. Eldridge, 424 U.S. 319": "verified",
            "REAL - Cleveland Bd. of Educ. v. Loudermill, 470 U.S. 532": "verified",
            "REAL - Goldberg v. Kelly, 397 U.S. 254": "verified",
            "REAL - Harlow v. Fitzgerald, 457 U.S. 800": "verified",
            "REAL - Anderson v. Creighton, 483 U.S. 635": "verified",
            "FAKE - Whitfield v. Mun. Auth. of Camden, 734 F.3d 291": "error/unverifiable",
            "REAL - Korematsu v. United States, 323 U.S. 214 (dissent)": "verified",
            "REAL - Plessy v. Ferguson, 163 U.S. 537 (dissent)": "verified",
            "REAL - Daubert v. Merrell Dow Pharms., 509 U.S. 579": "verified",
            "REAL - Hickman v. Taylor, 329 U.S. 495": "verified",
            "REAL - Oppenheimer Fund v. Sanders, 437 U.S. 340": "verified",
            "FAKE - Gallagher v. Consolidated Freightways, 789 F.3d 803": "error/unverifiable",
            "REAL - Marbury v. Madison, 5 U.S. 137": "verified",
            "REAL - Students for Fair Admissions v. Harvard, 600 U.S. 181": "verified",
            "REAL - Loper Bright v. Raimondo, 144 S. Ct. 2244": "verified",
        },
    },
    "k": {
        "file": "brief_k_subtle_traps.docx",
        "description": "SUBTLE TRAPS — Wrong volume, dissent-as-majority, holding swaps, wrong year",
        "expectations": {
            "REAL - Celotex v. Catrett, 477 U.S. 317": "verified",
            "TRAP - Heller wrong volume (555 instead of 554 U.S.)": "warning/error",
            "TRAP - Shelby County dissent as majority": "warning/error",
            "TRAP - Citizens United dissent as majority": "warning/error",
            "TRAP - Terry v. Ohio with Miranda's holding": "warning/error",
            "TRAP - Miranda v. Arizona with Terry's holding": "warning/error",
            "TRAP - McCulloch with Marbury's quote": "warning/error",
            "REAL - McCulloch v. Maryland, 17 U.S. 316": "verified",
            "TRAP - Brown v. Board wrong year (1955)": "warning/error",
            "TRAP - Gideon v. Wainwright wrong year (1964)": "warning/error",
            "REAL - Batson v. Kentucky, 476 U.S. 79": "verified",
            "REAL - Strickland v. Washington, 466 U.S. 668": "verified",
        },
    },
    "l": {
        "file": "brief_l_lookalikes.docx",
        "description": "LOOKALIKES — Near-miss names, party reversals, wrong court, In re off-by-one",
        "expectations": {
            "TRAP - Miranda v. State of Arizona (wrong name)": "verified/warning",
            "REAL - Miranda v. Arizona, 384 U.S. 436": "verified",
            "TRAP - Sullivan v. New York Times (reversed parties)": "verified/warning",
            "TRAP - Sawyer v. Youngstown Sheet & Tube (reversed)": "verified/warning",
            "REAL - United States v. Nixon, 418 U.S. 683": "verified",
            "REAL - Nixon v. Fitzgerald, 457 U.S. 731": "verified",
            "FAKE - United States v. Williams, 893 F.3d 1127": "error/unverifiable",
            "REAL - Palsgraf v. Long Island R.R., 248 N.Y. 339": "verified",
            "FAKE - Palsgraf v. Long Island R.R., 248 F.2d 339": "error/unverifiable",
            "REAL - In re Gault, 387 U.S. 1": "verified",
            "FAKE - In re Galt, 387 F.3d 295": "error/unverifiable",
            "REAL - Jacobson v. Massachusetts, 197 U.S. 11": "verified",
            "TRAP - Jacobson fabricated extension (unlimited authority)": "warning/error",
        },
    },
}

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
BOLD = "\033[1m"
RESET = "\033[0m"


def progress_callback(step: int, total: int, message: str):
    bar_len = 40
    filled = int(bar_len * step / total)
    bar = "█" * filled + "░" * (bar_len - filled)
    print(f"\r  [{bar}] {step}% — {message}", end="", flush=True)
    if step >= 100:
        print()


def run_brief(brief_key: str):
    info = BRIEFS[brief_key]
    file_path = os.path.join(BRIEF_DIR, info["file"])

    if not os.path.exists(file_path):
        print(f"{RED}ERROR: {file_path} not found. Run create_test_briefs.py first.{RESET}")
        return None

    print(f"\n{'='*80}")
    print(f"{BOLD}{BLUE}BRIEF {brief_key.upper()}: {info['description']}{RESET}")
    print(f"{'='*80}")
    print(f"  File: {info['file']}")
    print(f"  Model: {os.getenv('CLAUDE_MODEL', 'default')}")
    print()

    start = time.time()
    report = run_verification(file_path, info["file"], progress_callback=progress_callback)
    elapsed = time.time() - start

    print(f"\n  {BOLD}Results ({elapsed:.1f}s):{RESET}")
    print(f"  Total citations: {report.total_citations}")
    print(f"  {GREEN}Verified: {report.verified}{RESET}")
    print(f"  {YELLOW}Warnings: {report.warnings}{RESET}")
    print(f"  {RED}Errors: {report.errors}{RESET}")
    print(f"  Unverifiable: {report.unverifiable}")

    print(f"\n  {BOLD}Citation Details:{RESET}")
    print(f"  {'─'*76}")

    for i, cr in enumerate(report.citations, 1):
        ext = cr.extraction
        ver = cr.verification
        lkp = cr.lookup

        # Color-code status
        status = ver.status
        if status == "verified":
            status_str = f"{GREEN}VERIFIED{RESET}"
        elif status == "warning":
            status_str = f"{YELLOW}WARNING{RESET}"
        elif status == "error":
            status_str = f"{RED}ERROR{RESET}"
        else:
            status_str = f"UNVERIFIABLE"

        found_str = f"{GREEN}found{RESET}" if lkp.found else f"{RED}not found{RESET}"
        source = lkp.source or "none"

        print(f"\n  {BOLD}#{i}{RESET} {ext.case_name}")
        print(f"     Citation: {ext.citation_text}")
        print(f"     Lookup:   {found_str} (source: {source})")
        print(f"     Status:   {status_str} (confidence: {ver.confidence:.2f})")

        if ext.quoted_text:
            quote_preview = ext.quoted_text[:80] + "..." if len(ext.quoted_text) > 80 else ext.quoted_text
            print(f"     Quote:    \"{quote_preview}\"")
            if ver.quote_accuracy:
                qa_color = GREEN if ver.quote_accuracy == "exact" else (YELLOW if ver.quote_accuracy == "close" else RED)
                print(f"     Quote accuracy: {qa_color}{ver.quote_accuracy}{RESET}")
            if ver.quote_diff:
                print(f"     Quote diff: {ver.quote_diff[:100]}")

        if ext.characterization:
            char_preview = ext.characterization[:80] + "..." if len(ext.characterization) > 80 else ext.characterization
            print(f"     Characterization: \"{char_preview}\"")
            if ver.characterization_accuracy:
                ca_color = GREEN if ver.characterization_accuracy == "accurate" else (YELLOW if ver.characterization_accuracy == "misleading" else RED)
                print(f"     Char accuracy: {ca_color}{ver.characterization_accuracy}{RESET}")
            if ver.characterization_explanation:
                print(f"     Char explanation: {ver.characterization_explanation[:120]}")

        if ver.reasoning:
            print(f"     Reasoning: {ver.reasoning[:150]}")

    print(f"\n  {'─'*76}")

    # Print expectations
    print(f"\n  {BOLD}Expected Outcomes:{RESET}")
    for expectation, expected in info["expectations"].items():
        print(f"    • {expectation}: {expected}")

    # Save full report as JSON
    report_path = os.path.join(BRIEF_DIR, f"report_{brief_key}.json")
    with open(report_path, "w") as f:
        json.dump(report.to_dict(), f, indent=2)
    print(f"\n  Full report saved: {report_path}")

    return report


def main():
    parser = argparse.ArgumentParser(description="CiteVerify accuracy test runner")
    parser.add_argument("--brief", choices=["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "all"], default="all",
                       help="Which brief to test (default: all)")
    args = parser.parse_args()

    print(f"{BOLD}CiteVerify Accuracy Test Suite{RESET}")
    print(f"Model: {os.getenv('CLAUDE_MODEL', 'not set')}")
    print(f"CourtListener token: {'set' if os.getenv('COURTLISTENER_API_TOKEN') else 'NOT SET'}")

    briefs_to_run = list("abcdefghi") if args.brief == "all" else [args.brief]

    results = {}
    for key in briefs_to_run:
        results[key] = run_brief(key)

    # Summary
    print(f"\n{'='*80}")
    print(f"{BOLD}SUMMARY{RESET}")
    print(f"{'='*80}")
    for key, report in results.items():
        if report:
            print(f"  Brief {key.upper()}: {report.total_citations} citations — "
                  f"{GREEN}{report.verified} verified{RESET}, "
                  f"{YELLOW}{report.warnings} warnings{RESET}, "
                  f"{RED}{report.errors} errors{RESET}, "
                  f"{report.unverifiable} unverifiable")


if __name__ == "__main__":
    main()
