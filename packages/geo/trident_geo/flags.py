"""Flag-state derivation from the MMSI MID (Maritime Identification Digits) prefix.

The first three digits of an MMSI encode the country of registration. This is how
the Analyst derives flag state with zero external calls, and how shadow-fleet
flag-hopping is detected (frequent re-registration across MIDs).

This is a curated subset covering the major flags and the flags-of-convenience that
dominate chokepoint traffic and shadow-fleet activity. Full ITU list is ~250 entries.
"""
from __future__ import annotations

MID_TO_FLAG: dict[int, str] = {
    201: "Albania", 205: "Belgium", 209: "Cyprus", 210: "Cyprus", 211: "Germany",
    212: "Cyprus", 215: "Malta", 218: "Germany", 219: "Denmark", 220: "Denmark",
    224: "Spain", 225: "Spain", 226: "France", 227: "France", 228: "France",
    230: "Finland", 231: "Faroe Islands", 232: "United Kingdom", 233: "United Kingdom",
    234: "United Kingdom", 235: "United Kingdom", 236: "Gibraltar", 237: "Greece",
    238: "Croatia", 239: "Greece", 240: "Greece", 241: "Greece", 242: "Morocco",
    243: "Hungary", 244: "Netherlands", 245: "Netherlands", 246: "Netherlands",
    247: "Italy", 248: "Malta", 249: "Malta", 250: "Ireland", 251: "Iceland",
    253: "Luxembourg", 254: "Monaco", 255: "Madeira", 256: "Malta", 257: "Norway",
    258: "Norway", 259: "Norway", 261: "Poland", 262: "Montenegro", 263: "Portugal",
    265: "Sweden", 266: "Sweden", 269: "Switzerland", 271: "Turkey", 272: "Ukraine",
    273: "Russia", 304: "Antigua and Barbuda", 305: "Antigua and Barbuda",
    306: "Curacao", 307: "Aruba", 308: "Bahamas", 309: "Bahamas", 311: "Bahamas",
    312: "Belize", 314: "Barbados", 316: "Canada", 319: "Cayman Islands",
    321: "Costa Rica", 338: "United States", 341: "Saint Kitts and Nevis",
    351: "Panama", 352: "Panama", 353: "Panama", 354: "Panama", 355: "Panama",
    356: "Panama", 357: "Panama", 370: "Panama", 371: "Panama", 372: "Panama",
    373: "Panama", 374: "Panama", 351: "Panama", 366: "United States",
    367: "United States", 368: "United States", 369: "United States",
    374: "Panama", 376: "Saint Vincent", 377: "Saint Vincent", 378: "British Virgin Is.",
    401: "Afghanistan", 403: "Saudi Arabia", 405: "Bangladesh", 408: "Bahrain",
    412: "China", 413: "China", 414: "China", 416: "Taiwan", 419: "India",
    422: "Iran", 423: "Azerbaijan", 425: "Iraq", 428: "Israel", 431: "Japan",
    432: "Japan", 440: "South Korea", 441: "South Korea", 445: "North Korea",
    447: "Kuwait", 451: "Kyrgyzstan", 453: "Macao", 457: "Mongolia", 459: "Nepal",
    461: "Oman", 463: "Pakistan", 466: "Qatar", 470: "United Arab Emirates",
    471: "United Arab Emirates", 472: "Tajikistan", 473: "Yemen", 475: "Yemen",
    477: "Hong Kong", 525: "Indonesia", 533: "Malaysia", 563: "Singapore",
    564: "Singapore", 565: "Singapore", 566: "Singapore", 567: "Thailand",
    574: "Vietnam", 577: "Sri Lanka", 601: "South Africa", 605: "Algeria",
    613: "Cameroon", 615: "Congo", 621: "Djibouti", 622: "Egypt", 624: "Ethiopia",
    636: "Liberia", 637: "Liberia", 642: "Libya", 644: "Lesotho", 649: "Morocco",
    657: "Nigeria", 664: "Sudan", 667: "Sierra Leone", 671: "Togo", 672: "Tunisia",
    674: "Tanzania", 677: "Tanzania", 710: "Brazil", 720: "Bolivia", 725: "Chile",
    730: "Colombia", 735: "Ecuador", 750: "Guyana", 760: "Peru", 770: "Uruguay",
    775: "Venezuela",
}

# Flags-of-convenience commonly associated with shadow-fleet / sanctions evasion.
FLAGS_OF_CONVENIENCE = {
    "Panama", "Liberia", "Marshall Islands", "Bahamas", "Malta", "Cyprus",
    "Cook Islands", "Gabon", "Cameroon", "Palau", "Comoros", "Saint Kitts and Nevis",
    "Tanzania", "Togo", "Sierra Leone", "Curacao", "Barbados",
}


def flag_for_mmsi(mmsi: int | None) -> str | None:
    if mmsi is None:
        return None
    try:
        mid = int(str(int(mmsi))[:3])
    except (ValueError, TypeError):
        return None
    return MID_TO_FLAG.get(mid)


def is_flag_of_convenience(flag: str | None) -> bool:
    return bool(flag) and flag in FLAGS_OF_CONVENIENCE
