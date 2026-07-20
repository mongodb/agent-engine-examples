from __future__ import annotations

import os
import time
import logging
from datetime import UTC, datetime
from typing import Annotated

import httpx
from google.transit import gtfs_realtime_pb2
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from magenta_sdklanggraph import App

app = App(app_name="MTA Alerts Agent")

logger = logging.getLogger(__name__)

# MTA GTFS-RT subway alerts endpoint
_MTA_ALERTS_URL = (
    "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/camsys%2Fsubway-alerts"
)

_MTA_FEED_BASE = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/"

# Maps subway line letter/number to its MTA GTFS-RT trip updates feed path.
_LINE_TO_FEED: dict[str, str] = {
    "1": "nyct%2Fgtfs", "2": "nyct%2Fgtfs", "3": "nyct%2Fgtfs",
    "4": "nyct%2Fgtfs", "5": "nyct%2Fgtfs", "6": "nyct%2Fgtfs",
    "7": "nyct%2Fgtfs", "S": "nyct%2Fgtfs",
    "A": "nyct%2Fgtfs-ace", "C": "nyct%2Fgtfs-ace", "E": "nyct%2Fgtfs-ace",
    "B": "nyct%2Fgtfs-bdfm", "D": "nyct%2Fgtfs-bdfm",
    "F": "nyct%2Fgtfs-bdfm", "M": "nyct%2Fgtfs-bdfm",
    "G": "nyct%2Fgtfs-g",
    "J": "nyct%2Fgtfs-jz", "Z": "nyct%2Fgtfs-jz",
    "N": "nyct%2Fgtfs-nqrw", "Q": "nyct%2Fgtfs-nqrw",
    "R": "nyct%2Fgtfs-nqrw", "W": "nyct%2Fgtfs-nqrw",
    "L": "nyct%2Fgtfs-l",
    "SI": "nyct%2Fgtfs-si",
}

# Hardcoded from MTA static GTFS stops.txt (location_type=1 parent stations).
# Keys are canonical station names; values are lists of GTFS parent station IDs
# (multiple IDs when the same name covers stops on different lines/complexes).
_STOP_NAME_TO_IDS: dict[str, list[str]] = {
    '1 Av': ['L06'],
    '103 St': ['119', '624', 'A18'],
    '103 St-Corona Plaza': ['706'],
    '104 St': ['A63', 'J14'],
    '110 St': ['623'],
    '110 St-Malcolm X Plaza': ['227'],
    '111 St': ['705', 'A64', 'J13'],
    '116 St': ['226', '622', 'A16'],
    '116 St-Columbia University': ['117'],
    '121 St': ['J12'],
    '125 St': ['116', '225', '621', 'A15'],
    '135 St': ['224', 'A14'],
    '137 St-City College': ['115'],
    '138 St-Grand Concourse': ['416'],
    '14 St': ['132', 'A31', 'D19'],
    '14 St-Union Sq': ['635', 'L03', 'R20'],
    '145 St': ['114', '302', 'A12', 'D13'],
    '149 St-Grand Concourse': ['222', '415'],
    '15 St-Prospect Park': ['F25'],
    '155 St': ['A11', 'D12'],
    '157 St': ['113'],
    '161 St-Yankee Stadium': ['414', 'D11'],
    '163 St-Amsterdam Av': ['A10'],
    '167 St': ['413', 'D10'],
    '168 St': ['A09'],
    '168 St-Washington Hts': ['112'],
    '169 St': ['F02'],
    '170 St': ['412', 'D09'],
    '174 St': ['215'],
    '174-175 Sts': ['D08'],
    '175 St': ['A07'],
    '176 St': ['410'],
    '18 Av': ['B19', 'F30', 'N05'],
    '18 St': ['131'],
    '181 St': ['111', 'A06'],
    '182-183 Sts': ['D06'],
    '183 St': ['408'],
    '190 St': ['A05'],
    '191 St': ['110'],
    '2 Av': ['F14'],
    '20 Av': ['B20', 'N06'],
    '207 St': ['108'],
    '21 St': ['G24'],
    '21 St-Queensbridge': ['B04'],
    '215 St': ['107'],
    '219 St': ['207'],
    '225 St': ['206'],
    '23 St': ['130', 'A30', 'D18', 'R19'],
    '23 St-Baruch College': ['634'],
    '231 St': ['104'],
    '233 St': ['205'],
    '238 St': ['103'],
    '25 Av': ['B22'],
    '25 St': ['R35'],
    '28 St': ['129', '633', 'R18'],
    '3 Av': ['L05'],
    '3 Av-138 St': ['619'],
    '3 Av-149 St': ['221'],
    '30 Av': ['R04'],
    '33 St': ['632'],
    '33 St-Rawson St': ['716'],
    '34 St-Herald Sq': ['D17', 'R17'],
    '34 St-Hudson Yards': ['726'],
    '34 St-Penn Station': ['128', 'A28'],
    '36 Av': ['R06'],
    '36 St': ['G20', 'R36'],
    '39 Av-Dutch Kills': ['R08'],
    '4 Av-9 St': ['F23', 'R33'],
    '40 St-Lowery St': ['715'],
    '42 St-Bryant Pk': ['D16'],
    '42 St-Port Authority Bus Terminal': ['A27'],
    '45 St': ['R39'],
    '46 St': ['G18'],
    '46 St-Bliss St': ['714'],
    '47-50 Sts-Rockefeller Ctr': ['D15'],
    '49 St': ['R15'],
    '5 Av': ['724'],
    '5 Av/53 St': ['F12'],
    '5 Av/59 St': ['R13'],
    '50 St': ['126', 'A25', 'B14'],
    '51 St': ['630'],
    '52 St': ['713'],
    '53 St': ['R40'],
    '55 St': ['B15'],
    '57 St': ['B10'],
    '57 St-7 Av': ['R14'],
    '59 St': ['629', 'R41'],
    '59 St-Columbus Circle': ['125', 'A24'],
    '6 Av': ['L02'],
    '61 St-Woodside': ['712'],
    '62 St': ['B16'],
    '63 Dr-Rego Park': ['G10'],
    '65 St': ['G15'],
    '66 St-Lincoln Center': ['124'],
    '67 Av': ['G09'],
    '68 St-Hunter College': ['628'],
    '69 St': ['711'],
    '7 Av': ['D14', 'D25', 'F24'],
    '71 St': ['B17'],
    '72 St': ['123', 'A22', 'Q03'],
    '74 St-Broadway': ['710'],
    '75 Av': ['F07'],
    '75 St-Elderts Ln': ['J17'],
    '77 St': ['627', 'R43'],
    '79 St': ['122', 'B18'],
    '8 Av': ['L01', 'N02'],
    '8 St-NYU': ['R21'],
    '80 St': ['A59'],
    '81 St-Museum of Natural History': ['A21'],
    '82 St-Jackson Hts': ['709'],
    '85 St-Forest Pkwy': ['J16'],
    '86 St': ['121', '626', 'A20', 'N10', 'Q04', 'R44'],
    '88 St': ['A60'],
    '9 Av': ['B12'],
    '90 St-Elmhurst Av': ['708'],
    '96 St': ['120', '625', 'A19', 'Q05'],
    'Alabama Av': ['J24'],
    'Allerton Av': ['210'],
    'Annadale': ['S17'],
    'Aqueduct Racetrack': ['H01'],
    'Aqueduct-N Conduit Av': ['H02'],
    'Arthur Kill': ['S11'],
    'Astor Pl': ['636'],
    'Astoria Blvd': ['R03'],
    'Astoria-Ditmars Blvd': ['R01'],
    'Atlantic Av': ['L24'],
    'Atlantic Av-Barclays Ctr': ['235', 'D24', 'R31'],
    'Avenue H': ['D32'],
    'Avenue I': ['F31'],
    'Avenue J': ['D33'],
    'Avenue M': ['D34'],
    'Avenue N': ['F33'],
    'Avenue P': ['F34'],
    'Avenue U': ['D37', 'F36', 'N09'],
    'Avenue X': ['F38'],
    'Bay 50 St': ['B23'],
    'Bay Pkwy': ['B21', 'F32', 'N07'],
    'Bay Ridge Av': ['R42'],
    'Bay Ridge-95 St': ['R45'],
    'Bay Terrace': ['S20'],
    'Baychester Av': ['502'],
    'Beach 105 St': ['H14'],
    'Beach 25 St': ['H10'],
    'Beach 36 St': ['H09'],
    'Beach 44 St': ['H08'],
    'Beach 60 St': ['H07'],
    'Beach 67 St': ['H06'],
    'Beach 90 St': ['H12'],
    'Beach 98 St': ['H13'],
    'Bedford Av': ['L08'],
    'Bedford Park Blvd': ['D03'],
    'Bedford Park Blvd-Lehman College': ['405'],
    'Bedford-Nostrand Avs': ['G33'],
    'Bergen St': ['236', 'F20'],
    'Beverley Rd': ['D29'],
    'Beverly Rd': ['245'],
    'Bleecker St': ['637'],
    'Borough Hall': ['232', '423'],
    'Botanic Garden': ['S04'],
    'Bowery': ['M19'],
    'Bowling Green': ['420'],
    'Briarwood': ['F05'],
    'Brighton Beach': ['D40'],
    'Broad Channel': ['H04'],
    'Broad St': ['M23'],
    'Broadway': ['G30', 'R05'],
    'Broadway Junction': ['A51', 'J27', 'L22'],
    'Broadway-Lafayette St': ['D21'],
    'Bronx Park East': ['212'],
    'Brook Av': ['618'],
    'Brooklyn Bridge-City Hall': ['640'],
    'Buhre Av': ['602'],
    'Burke Av': ['209'],
    'Burnside Av': ['409'],
    'Bushwick Av-Aberdeen St': ['L21'],
    'Canal St': ['135', '639', 'A34', 'M20', 'Q01', 'R23'],
    'Canarsie-Rockaway Pkwy': ['L29'],
    'Carroll St': ['F21'],
    'Castle Hill Av': ['607'],
    'Cathedral Pkwy (110 St)': ['118', 'A17'],
    'Central Av': ['M10'],
    'Chambers St': ['137', 'A36', 'M21'],
    'Chauncey St': ['J28'],
    'Christopher St-Stonewall': ['133'],
    'Church Av': ['244', 'D28', 'F27'],
    'City Hall': ['R24'],
    'Clark St': ['231'],
    'Classon Av': ['G34'],
    'Cleveland St': ['J22'],
    'Clifton': ['S28'],
    'Clinton-Washington Avs': ['A44', 'G35'],
    'Coney Island-Stillwell Av': ['D43'],
    'Cortelyou Rd': ['D30'],
    'Cortlandt St': ['R25'],
    'Court Sq': ['719', 'G22'],
    'Court Sq-23 St': ['F09'],
    'Court St': ['R28'],
    'Crescent St': ['J20'],
    'Crown Hts-Utica Av': ['250'],
    'Cypress Av': ['617'],
    'Cypress Hills': ['J19'],
    'DeKalb Av': ['L16', 'R30'],
    'Delancey St-Essex St': ['F15', 'M18'],
    'Ditmas Av': ['F29'],
    'Dongan Hills': ['S25'],
    'Dyckman St': ['109', 'A03'],
    "E 143 St-St Mary's St": ['616'],
    'E 149 St': ['615'],
    'E 180 St': ['213'],
    'East 105 St': ['L28'],
    'East Broadway': ['F16'],
    'Eastchester-Dyre Av': ['501'],
    'Eastern Pkwy-Brooklyn Museum': ['238'],
    'Elder Av': ['611'],
    'Elmhurst Av': ['G13'],
    'Eltingville': ['S18'],
    'Euclid Av': ['A55'],
    'Far Rockaway-Mott Av': ['H11'],
    'Flatbush Av-Brooklyn College': ['247'],
    'Flushing Av': ['G31', 'M12'],
    'Flushing-Main St': ['701'],
    'Fordham Rd': ['407', 'D05'],
    'Forest Av': ['M05'],
    'Forest Hills-71 Av': ['G08'],
    'Fort Hamilton Pkwy': ['B13', 'F26', 'N03'],
    'Franklin Av': ['A45', 'S01'],
    'Franklin Av-Medgar Evers College': ['239'],
    'Franklin St': ['136'],
    'Freeman St': ['216'],
    'Fresh Pond Rd': ['M04'],
    'Fulton St': ['229', '418', 'A38', 'G36', 'M22'],
    'Gates Av': ['J30'],
    'Graham Av': ['L11'],
    'Grand Army Plaza': ['237'],
    'Grand Av-Newtown': ['G12'],
    'Grand Central-42 St': ['631', '723', '901'],
    'Grand St': ['D22', 'L12'],
    'Grant Av': ['A57'],
    'Grant City': ['S23'],
    'Grasmere': ['S27'],
    'Great Kills': ['S19'],
    'Greenpoint Av': ['G26'],
    'Gun Hill Rd': ['208', '503'],
    'Halsey St': ['J29', 'L19'],
    'Harlem-148 St': ['301'],
    'Hewes St': ['M14'],
    'High St': ['A40'],
    'Houston St': ['134'],
    'Howard Beach-JFK Airport': ['H03'],
    'Hoyt St': ['233'],
    'Hoyt-Schermerhorn Sts': ['A42'],
    'Huguenot': ['S16'],
    'Hunters Point Av': ['720'],
    'Hunts Point Av': ['613'],
    'Intervale Av': ['218'],
    'Inwood-207 St': ['A02'],
    'Jackson Av': ['220'],
    'Jackson Hts-Roosevelt Av': ['G14'],
    'Jamaica Center-Parsons/Archer': ['G05'],
    'Jamaica-179 St': ['F01'],
    'Jamaica-Van Wyck': ['G07'],
    'Jay St-MetroTech': ['A41', 'R29'],
    'Jefferson Av': ['S24'],
    'Jefferson St': ['L15'],
    'Junction Blvd': ['707'],
    'Junius St': ['254'],
    'Kew Gardens-Union Tpke': ['F06'],
    'Kings Hwy': ['D35', 'F35', 'N08'],
    'Kingsbridge Rd': ['406', 'D04'],
    'Kingston Av': ['249'],
    'Kingston-Throop Avs': ['A47'],
    'Knickerbocker Av': ['M09'],
    'Kosciuszko St': ['J31'],
    'Lafayette Av': ['A43'],
    'Lexington Av/53 St': ['F11'],
    'Lexington Av/59 St': ['R11'],
    'Lexington Av/63 St': ['B08'],
    'Liberty Av': ['A52'],
    'Livonia Av': ['L26'],
    'Longwood Av': ['614'],
    'Lorimer St': ['L10', 'M13'],
    'Marble Hill-225 St': ['106'],
    'Marcy Av': ['M16'],
    'Metropolitan Av': ['G29'],
    'Mets-Willets Point': ['702'],
    'Middle Village-Metropolitan Av': ['M01'],
    'Middletown Rd': ['603'],
    'Montrose Av': ['L13'],
    'Morgan Av': ['L14'],
    'Morris Park': ['505'],
    'Morrison Av-Soundview': ['610'],
    'Mosholu Pkwy': ['402'],
    'Mt Eden Av': ['411'],
    'Myrtle Av': ['M11'],
    'Myrtle-Willoughby Avs': ['G32'],
    'Myrtle-Wyckoff Avs': ['L17', 'M08'],
    'Nassau Av': ['G28'],
    'Neck Rd': ['D38'],
    'Neptune Av': ['F39'],
    'Nereid Av': ['204'],
    'Nevins St': ['234'],
    'New Dorp': ['S22'],
    'New Lots Av': ['257', 'L27'],
    'New Utrecht Av': ['N04'],
    'Newkirk Av-Little Haiti': ['246'],
    'Newkirk Plaza': ['D31'],
    'Northern Blvd': ['G16'],
    'Norwood Av': ['J21'],
    'Norwood-205 St': ['D01'],
    'Nostrand Av': ['248', 'A46'],
    'Oakwood Heights': ['S21'],
    'Ocean Pkwy': ['D41'],
    'Old Town': ['S26'],
    'Ozone Park-Lefferts Blvd': ['A65'],
    'Park Pl': ['S03'],
    'Park Place': ['228'],
    'Parkchester': ['608'],
    'Parkside Av': ['D27'],
    'Parsons Blvd': ['F03'],
    'Pelham Bay Park': ['601'],
    'Pelham Pkwy': ['211', '504'],
    'Pennsylvania Av': ['255'],
    'Pleasant Plains': ['S14'],
    'President St-Medgar Evers College': ['241'],
    'Prince St': ['R22'],
    "Prince's Bay": ['S15'],
    'Prospect Av': ['219', 'R34'],
    'Prospect Park': ['D26'],
    'Queens Plaza': ['G21'],
    'Queensboro Plaza': ['718', 'R09'],
    'Ralph Av': ['A49'],
    'Rector St': ['139', 'R26'],
    'Richmond Valley': ['S13'],
    'Rockaway Av': ['253', 'A50'],
    'Rockaway Blvd': ['A61'],
    'Rockaway Park-Beach 116 St': ['H15'],
    'Roosevelt Island': ['B06'],
    'Saratoga Av': ['252'],
    'Seneca Av': ['M06'],
    'Sheepshead Bay': ['D39'],
    'Shepherd Av': ['A54'],
    'Simpson St': ['217'],
    'Smith-9 Sts': ['F22'],
    'South Ferry': ['142'],
    'Spring St': ['638', 'A33'],
    'St George': ['S31'],
    'St Lawrence Av': ['609'],
    'Stapleton': ['S29'],
    'Steinway St': ['G19'],
    'Sterling St': ['242'],
    'Sutphin Blvd': ['F04'],
    'Sutphin Blvd-Archer Av-JFK Airport': ['G06'],
    'Sutter Av': ['L25'],
    'Sutter Av-Rutland Rd': ['251'],
    'Times Sq-42 St': ['127', '725', '902', 'R16'],
    'Tompkinsville': ['S30'],
    'Tottenville': ['S09'],
    'Tremont Av': ['D07'],
    'Union St': ['R32'],
    'Utica Av': ['A48'],
    'Van Cortlandt Park-242 St': ['101'],
    'Van Siclen Av': ['256', 'A53', 'J23'],
    'Vernon Blvd-Jackson Av': ['721'],
    'W 4 St-Wash Sq': ['A32', 'D20'],
    'W 8 St-NY Aquarium': ['D42'],
    'WTC Cortlandt': ['138'],
    'Wakefield-241 St': ['201'],
    'Wall St': ['230', '419'],
    'West Farms Sq-E Tremont Av': ['214'],
    'Westchester Sq-E Tremont Av': ['604'],
    'Whitehall St-South Ferry': ['R27'],
    'Whitlock Av': ['612'],
    'Wilson Av': ['L20'],
    'Winthrop St': ['243'],
    'Woodhaven Blvd': ['G11', 'J15'],
    'Woodlawn': ['401'],
    'World Trade Center': ['E01'],
    'York St': ['F18'],
    'Zerega Av': ['606'],
}

# Maps GTFS parent station ID to the subway routes that serve it.
# Derived from MTA static GTFS trips.txt + stop_times.txt.
_STOP_ID_TO_ROUTES: dict[str, list[str]] = {
    '101': ['1'],
    '103': ['1'],
    '104': ['1'],
    '106': ['1'],
    '107': ['1'],
    '108': ['1'],
    '109': ['1'],
    '110': ['1'],
    '111': ['1'],
    '112': ['1'],
    '113': ['1'],
    '114': ['1'],
    '115': ['1'],
    '116': ['1'],
    '117': ['1'],
    '118': ['1'],
    '119': ['1'],
    '120': ['1', '2', '3'],
    '121': ['1', '2'],
    '122': ['1', '2'],
    '123': ['1', '2', '3'],
    '124': ['1', '2'],
    '125': ['1', '2'],
    '126': ['1', '2'],
    '127': ['1', '2', '3'],
    '128': ['1', '2', '3'],
    '129': ['1', '2'],
    '130': ['1', '2'],
    '131': ['1', '2'],
    '132': ['1', '2', '3'],
    '133': ['1', '2'],
    '134': ['1', '2'],
    '135': ['1', '2'],
    '136': ['1', '2'],
    '137': ['1', '2', '3'],
    '138': ['1'],
    '139': ['1'],
    '142': ['1'],
    '201': ['2'],
    '204': ['2', '5'],
    '205': ['2', '5'],
    '206': ['2', '5'],
    '207': ['2', '5'],
    '208': ['2', '5'],
    '209': ['2', '5'],
    '210': ['2', '5'],
    '211': ['2', '5'],
    '212': ['2', '5'],
    '213': ['2', '5'],
    '214': ['2', '5'],
    '215': ['2', '5'],
    '216': ['2', '5'],
    '217': ['2', '5'],
    '218': ['2', '5'],
    '219': ['2', '5'],
    '220': ['2', '5'],
    '221': ['2', '5'],
    '222': ['2', '5'],
    '224': ['2', '3'],
    '225': ['2', '3'],
    '226': ['2', '3'],
    '227': ['2', '3'],
    '228': ['2', '3'],
    '229': ['2', '3'],
    '230': ['2', '3'],
    '231': ['2', '3'],
    '232': ['2', '3'],
    '233': ['2', '3'],
    '234': ['2', '3', '4', '5'],
    '235': ['2', '3', '4', '5'],
    '236': ['2', '3', '4'],
    '237': ['2', '3', '4'],
    '238': ['2', '3', '4'],
    '239': ['2', '3', '4', '5'],
    '241': ['2', '5'],
    '242': ['2', '5'],
    '243': ['2', '5'],
    '244': ['2', '5'],
    '245': ['2', '5'],
    '246': ['2', '5'],
    '247': ['2', '5'],
    '248': ['2', '3', '4', '5'],
    '249': ['2', '3', '4', '5'],
    '250': ['2', '3', '4', '5'],
    '251': ['2', '3', '4', '5'],
    '252': ['2', '3', '4', '5'],
    '253': ['2', '3', '4', '5'],
    '254': ['2', '3', '4', '5'],
    '255': ['2', '3', '4', '5'],
    '256': ['2', '3', '4', '5'],
    '257': ['2', '3', '4', '5'],
    '301': ['3'],
    '302': ['3'],
    '401': ['4'],
    '402': ['4'],
    '405': ['4'],
    '406': ['4'],
    '407': ['4'],
    '408': ['4'],
    '409': ['4'],
    '410': ['4'],
    '411': ['4'],
    '412': ['4'],
    '413': ['4'],
    '414': ['4'],
    '415': ['4'],
    '416': ['4', '5'],
    '418': ['4', '5'],
    '419': ['4', '5'],
    '420': ['4', '5'],
    '423': ['4', '5'],
    '501': ['5'],
    '502': ['5'],
    '503': ['5'],
    '504': ['5'],
    '505': ['5'],
    '601': ['6', '6X'],
    '602': ['6', '6X'],
    '603': ['6', '6X'],
    '604': ['6', '6X'],
    '606': ['6', '6X'],
    '607': ['6', '6X'],
    '608': ['6', '6X'],
    '609': ['6'],
    '610': ['6'],
    '611': ['6'],
    '612': ['6'],
    '613': ['6', '6X'],
    '614': ['6'],
    '615': ['6'],
    '616': ['6'],
    '617': ['6'],
    '618': ['6'],
    '619': ['6', '6X'],
    '621': ['4', '5', '6', '6X'],
    '622': ['4', '6', '6X'],
    '623': ['4', '6', '6X'],
    '624': ['4', '6', '6X'],
    '625': ['4', '6', '6X'],
    '626': ['4', '5', '6', '6X'],
    '627': ['4', '6', '6X'],
    '628': ['4', '6', '6X'],
    '629': ['4', '5', '6', '6X'],
    '630': ['4', '6', '6X'],
    '631': ['4', '5', '6', '6X'],
    '632': ['4', '6', '6X'],
    '633': ['4', '6', '6X'],
    '634': ['4', '6', '6X'],
    '635': ['4', '5', '6', '6X'],
    '636': ['4', '6', '6X'],
    '637': ['4', '6', '6X'],
    '638': ['4', '6', '6X'],
    '639': ['4', '6', '6X'],
    '640': ['4', '5', '6', '6X'],
    '701': ['7', '7X'],
    '702': ['7', '7X'],
    '705': ['7'],
    '706': ['7'],
    '707': ['7', '7X'],
    '708': ['7'],
    '709': ['7'],
    '710': ['7', '7X'],
    '711': ['7', '7X'],
    '712': ['7', '7X'],
    '713': ['7', '7X'],
    '714': ['7', '7X'],
    '715': ['7', '7X'],
    '716': ['7', '7X'],
    '718': ['7', '7X'],
    '719': ['7', '7X'],
    '720': ['7', '7X'],
    '721': ['7', '7X'],
    '723': ['7', '7X'],
    '724': ['7', '7X'],
    '725': ['7', '7X'],
    '726': ['7', '7X'],
    '901': ['GS'],
    '902': ['GS'],
    'A02': ['A'],
    'A03': ['A'],
    'A05': ['A'],
    'A06': ['A'],
    'A07': ['A'],
    'A09': ['A', 'C'],
    'A10': ['A', 'C'],
    'A11': ['A', 'C'],
    'A12': ['A', 'C'],
    'A14': ['A', 'B', 'C'],
    'A15': ['A', 'B', 'C', 'D'],
    'A16': ['A', 'B', 'C'],
    'A17': ['A', 'B', 'C'],
    'A18': ['A', 'B', 'C'],
    'A19': ['A', 'B', 'C'],
    'A20': ['A', 'B', 'C'],
    'A21': ['A', 'B', 'C'],
    'A22': ['A', 'B', 'C'],
    'A24': ['A', 'B', 'C', 'D'],
    'A25': ['A', 'C', 'E'],
    'A27': ['A', 'C', 'E'],
    'A28': ['A', 'C', 'E'],
    'A30': ['A', 'C', 'E'],
    'A31': ['A', 'C', 'E'],
    'A32': ['A', 'C', 'E'],
    'A33': ['A', 'C', 'E'],
    'A34': ['A', 'C', 'E'],
    'A36': ['A', 'C'],
    'A38': ['A', 'C'],
    'A40': ['A', 'C'],
    'A41': ['A', 'C', 'F', 'FX'],
    'A42': ['A', 'C', 'G'],
    'A43': ['A', 'C'],
    'A44': ['A', 'C'],
    'A45': ['A', 'C'],
    'A46': ['A', 'C'],
    'A47': ['A', 'C'],
    'A48': ['A', 'C'],
    'A49': ['A', 'C'],
    'A50': ['A', 'C'],
    'A51': ['A', 'C'],
    'A52': ['A', 'C'],
    'A53': ['A', 'C'],
    'A54': ['A', 'C'],
    'A55': ['A', 'C'],
    'A57': ['A'],
    'A59': ['A'],
    'A60': ['A'],
    'A61': ['A'],
    'A63': ['A'],
    'A64': ['A'],
    'A65': ['A'],
    'B04': ['F', 'M'],
    'B06': ['F', 'M'],
    'B08': ['F', 'M', 'N', 'Q', 'R'],
    'B10': ['F', 'M'],
    'B12': ['D', 'R', 'W'],
    'B13': ['D'],
    'B14': ['D'],
    'B15': ['D'],
    'B16': ['D', 'R', 'W'],
    'B17': ['D'],
    'B18': ['D'],
    'B19': ['D'],
    'B20': ['D'],
    'B21': ['D', 'R', 'W'],
    'B22': ['D'],
    'B23': ['D'],
    'D01': ['D'],
    'D03': ['B', 'D'],
    'D04': ['B', 'D'],
    'D05': ['B', 'D'],
    'D06': ['B', 'D'],
    'D07': ['B', 'D'],
    'D08': ['B', 'D'],
    'D09': ['B', 'D'],
    'D10': ['B', 'D'],
    'D11': ['B', 'D'],
    'D12': ['B', 'D'],
    'D13': ['B', 'D'],
    'D14': ['B', 'D', 'E'],
    'D15': ['B', 'D', 'F', 'FX', 'M'],
    'D16': ['B', 'D', 'F', 'FX', 'M'],
    'D17': ['B', 'D', 'F', 'FX', 'M'],
    'D18': ['F', 'FX', 'M'],
    'D19': ['F', 'FX', 'M'],
    'D20': ['B', 'D', 'F', 'FX', 'M'],
    'D21': ['B', 'D', 'F', 'FX', 'M'],
    'D22': ['B', 'D'],
    'D24': ['B', 'Q'],
    'D25': ['B', 'Q'],
    'D26': ['B', 'FS', 'Q'],
    'D27': ['Q'],
    'D28': ['B', 'Q'],
    'D29': ['Q'],
    'D30': ['Q'],
    'D31': ['B', 'Q'],
    'D32': ['Q'],
    'D33': ['Q'],
    'D34': ['Q'],
    'D35': ['B', 'Q'],
    'D37': ['Q'],
    'D38': ['Q'],
    'D39': ['B', 'Q'],
    'D40': ['B', 'Q'],
    'D41': ['Q'],
    'D42': ['F', 'FX', 'Q'],
    'D43': ['D', 'F', 'FX', 'N', 'Q'],
    'E01': ['E'],
    'F01': ['E', 'F', 'FX'],
    'F02': ['E', 'F', 'FX'],
    'F03': ['E', 'F', 'FX'],
    'F04': ['E', 'F', 'FX'],
    'F05': ['E', 'F', 'FX'],
    'F06': ['E', 'F', 'FX'],
    'F07': ['E', 'F', 'FX'],
    'F09': ['E', 'F', 'FX'],
    'F11': ['E', 'F', 'FX'],
    'F12': ['E', 'F', 'FX'],
    'F14': ['F', 'FX'],
    'F15': ['F', 'FX'],
    'F16': ['F', 'FX'],
    'F18': ['F', 'FX'],
    'F20': ['F', 'G'],
    'F21': ['F', 'G'],
    'F22': ['F', 'G'],
    'F23': ['F', 'G'],
    'F24': ['F', 'FX', 'G'],
    'F25': ['F', 'G'],
    'F26': ['F', 'G'],
    'F27': ['F', 'FX', 'G'],
    'F29': ['F', 'FX'],
    'F30': ['F', 'FX'],
    'F31': ['F', 'FX'],
    'F32': ['F', 'FX'],
    'F33': ['F', 'FX'],
    'F34': ['F', 'FX'],
    'F35': ['F', 'FX'],
    'F36': ['F', 'FX'],
    'F38': ['F', 'FX'],
    'F39': ['F', 'FX'],
    'G05': ['E', 'J', 'Z'],
    'G06': ['E', 'J', 'Z'],
    'G07': ['E'],
    'G08': ['E', 'F', 'FX', 'M', 'R'],
    'G09': ['E', 'F', 'M', 'R'],
    'G10': ['E', 'F', 'M', 'R'],
    'G11': ['E', 'F', 'M', 'R'],
    'G12': ['E', 'F', 'M', 'R'],
    'G13': ['E', 'F', 'M', 'R'],
    'G14': ['E', 'F', 'FX', 'M', 'R'],
    'G15': ['E', 'F', 'M', 'R'],
    'G16': ['E', 'F', 'M', 'R'],
    'G18': ['E', 'F', 'M', 'R'],
    'G19': ['E', 'F', 'M', 'R'],
    'G20': ['E', 'F', 'M', 'R'],
    'G21': ['E', 'F', 'FX', 'R'],
    'G22': ['G'],
    'G24': ['G'],
    'G26': ['G'],
    'G28': ['G'],
    'G29': ['G'],
    'G30': ['G'],
    'G31': ['G'],
    'G32': ['G'],
    'G33': ['G'],
    'G34': ['G'],
    'G35': ['G'],
    'G36': ['G'],
    'H01': ['A'],
    'H02': ['A'],
    'H03': ['A'],
    'H04': ['A', 'H'],
    'H06': ['A'],
    'H07': ['A'],
    'H08': ['A'],
    'H09': ['A'],
    'H10': ['A'],
    'H11': ['A'],
    'H12': ['A', 'H'],
    'H13': ['A', 'H'],
    'H14': ['A', 'H'],
    'H15': ['A', 'H'],
    'J12': ['J', 'Z'],
    'J13': ['J'],
    'J14': ['J', 'Z'],
    'J15': ['J', 'Z'],
    'J16': ['J'],
    'J17': ['J', 'Z'],
    'J19': ['J'],
    'J20': ['J', 'Z'],
    'J21': ['J', 'Z'],
    'J22': ['J'],
    'J23': ['J', 'Z'],
    'J24': ['J', 'Z'],
    'J27': ['J', 'Z'],
    'J28': ['J', 'Z'],
    'J29': ['J'],
    'J30': ['J', 'Z'],
    'J31': ['J'],
    'L01': ['L'],
    'L02': ['L'],
    'L03': ['L'],
    'L05': ['L'],
    'L06': ['L'],
    'L08': ['L'],
    'L10': ['L'],
    'L11': ['L'],
    'L12': ['L'],
    'L13': ['L'],
    'L14': ['L'],
    'L15': ['L'],
    'L16': ['L'],
    'L17': ['L'],
    'L19': ['L'],
    'L20': ['L'],
    'L21': ['L'],
    'L22': ['L'],
    'L24': ['L'],
    'L25': ['L'],
    'L26': ['L'],
    'L27': ['L'],
    'L28': ['L'],
    'L29': ['L'],
    'M01': ['M'],
    'M04': ['M'],
    'M05': ['M'],
    'M06': ['M'],
    'M08': ['M'],
    'M09': ['M'],
    'M10': ['M'],
    'M11': ['J', 'M', 'Z'],
    'M12': ['J', 'M'],
    'M13': ['J', 'M'],
    'M14': ['J', 'M'],
    'M16': ['J', 'M', 'Z'],
    'M18': ['J', 'M', 'Z'],
    'M19': ['J', 'Z'],
    'M20': ['J', 'Z'],
    'M21': ['J', 'Z'],
    'M22': ['J', 'Z'],
    'M23': ['J', 'Z'],
    'N02': ['N', 'W'],
    'N03': ['N', 'W'],
    'N04': ['N', 'W'],
    'N05': ['N', 'W'],
    'N06': ['N', 'W'],
    'N07': ['N', 'W'],
    'N08': ['N', 'W'],
    'N09': ['N', 'W'],
    'N10': ['N', 'W'],
    'Q01': ['N', 'Q'],
    'Q03': ['N', 'Q', 'R'],
    'Q04': ['N', 'Q', 'R'],
    'Q05': ['N', 'Q', 'R'],
    'R01': ['N', 'W'],
    'R03': ['N', 'W'],
    'R04': ['N', 'W'],
    'R05': ['N', 'W'],
    'R06': ['N', 'W'],
    'R08': ['N', 'W'],
    'R09': ['N', 'W'],
    'R11': ['N', 'R', 'W'],
    'R13': ['N', 'R', 'W'],
    'R14': ['N', 'Q', 'R', 'W'],
    'R15': ['N', 'Q', 'R', 'W'],
    'R16': ['N', 'Q', 'R', 'W'],
    'R17': ['N', 'Q', 'R', 'W'],
    'R18': ['N', 'Q', 'R', 'W'],
    'R19': ['N', 'Q', 'R', 'W'],
    'R20': ['N', 'Q', 'R', 'W'],
    'R21': ['N', 'Q', 'R', 'W'],
    'R22': ['N', 'Q', 'R', 'W'],
    'R23': ['N', 'R', 'W'],
    'R24': ['N', 'R', 'W'],
    'R25': ['N', 'R', 'W'],
    'R26': ['N', 'R', 'W'],
    'R27': ['N', 'R', 'W'],
    'R28': ['N', 'R', 'W'],
    'R29': ['N', 'R', 'W'],
    'R30': ['B', 'D', 'N', 'Q', 'R', 'W'],
    'R31': ['D', 'N', 'R', 'W'],
    'R32': ['D', 'N', 'R', 'W'],
    'R33': ['D', 'N', 'R', 'W'],
    'R34': ['D', 'N', 'R', 'W'],
    'R35': ['D', 'N', 'R', 'W'],
    'R36': ['D', 'N', 'R', 'W'],
    'R39': ['N', 'R', 'W'],
    'R40': ['N', 'R', 'W'],
    'R41': ['N', 'R', 'W'],
    'R42': ['R'],
    'R43': ['R'],
    'R44': ['R'],
    'R45': ['R'],
    'S01': ['FS'],
    'S03': ['FS'],
    'S04': ['FS'],
    'S09': ['SI'],
    'S11': ['SI'],
    'S13': ['SI'],
    'S14': ['SI'],
    'S15': ['SI'],
    'S16': ['SI'],
    'S17': ['SI'],
    'S18': ['SI'],
    'S19': ['SI'],
    'S20': ['SI'],
    'S21': ['SI'],
    'S22': ['SI'],
    'S23': ['SI'],
    'S24': ['SI'],
    'S25': ['SI'],
    'S26': ['SI'],
    'S27': ['SI'],
    'S28': ['SI'],
    'S29': ['SI'],
    'S30': ['SI'],
    'S31': ['SI'],
}

# Map human-readable direction names to GTFS-RT direction_id values.
# MTA subway: 0 = southbound/downtown, 1 = northbound/uptown.
_DIRECTION_MAP: dict[str, int] = {
    "north": 1,
    "uptown": 1,
    "northbound": 1,
    "south": 0,
    "downtown": 0,
    "southbound": 0,
}


def _direction_ids(directions: list[str]) -> set[int] | None:
    """Convert direction names to GTFS direction_id integers, or None for 'all'."""
    if not directions:
        return None
    ids: set[int] = set()
    for d in directions:
        key = d.strip().lower()
        if key in _DIRECTION_MAP:
            ids.add(_DIRECTION_MAP[key])
    return ids if ids else None


def _format_active_periods(alert) -> str:
    """Return a human-readable string describing an alert's active periods."""
    if not alert.active_period:
        return "Active: always"
    parts: list[str] = []
    for period in alert.active_period:
        start = period.start if period.start else None
        end = period.end if period.end else None
        if start and end:
            s = datetime.fromtimestamp(start, tz=UTC).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            e = datetime.fromtimestamp(end, tz=UTC).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            parts.append(f"{s} – {e}")
        elif start:
            s = datetime.fromtimestamp(start, tz=UTC).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            parts.append(f"from {s}")
        elif end:
            e = datetime.fromtimestamp(end, tz=UTC).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            parts.append(f"until {e}")
        else:
            parts.append("always")
    return "Active: " + "; ".join(parts)


def _is_currently_active(alert) -> bool:
    """Return True if the alert is active right now based on its active_period."""
    if not alert.active_period:
        return True  # No period specified means always active
    now = time.time()
    for period in alert.active_period:
        start = period.start if period.start else None
        end = period.end if period.end else None
        after_start = start is None or now >= start
        before_end = end is None or now <= end
        if after_start and before_end:
            return True
    return False


@app.tool(is_local=True)
def get_subway_alerts(
    lines: Annotated[list[str], "Subway line identifiers, e.g. ['A', '1', 'L']"],
    directions: Annotated[
        list[str],
        "Directions to filter by: 'north'/'uptown' or 'south'/'downtown'. "
        "Pass an empty list for all directions.",
    ],
    active_only: Annotated[
        bool,
        "If True, only return alerts that are currently active based on their "
        "active_period. Defaults to False (return all matching alerts).",
    ] = False,
) -> str:
    """
    Fetch MTA subway service alerts for the given lines and directions.
    Each alert includes its active time window(s). Pass active_only=True to
    filter out alerts that are not currently in effect.
    """
    try:
        response = httpx.get(_MTA_ALERTS_URL, timeout=10)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return f"Failed to fetch MTA alerts: {exc}"

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)

    target_lines = {line.strip().upper() for line in lines}
    dir_ids = _direction_ids(directions)

    results: list[str] = []

    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue
        alert = entity.alert

        # Check whether any informed entity matches the requested lines/directions.
        matched = False
        for informed in alert.informed_entity:
            route = informed.route_id.upper() if informed.route_id else ""
            if route not in target_lines:
                continue
            if dir_ids is not None and informed.HasField("trip"):
                if informed.trip.direction_id not in dir_ids:
                    continue
            elif dir_ids is not None and not informed.HasField("trip"):
                # Alert has no direction specificity — include it regardless of filter.
                pass
            matched = True
            break

        if not matched:
            continue

        if active_only and not _is_currently_active(alert):
            continue

        # Extract human-readable text (prefer English translations).
        def _text(translated) -> str:
            for t in translated.translation:
                if not t.language or t.language.startswith("en"):
                    return t.text
            return translated.translation[0].text if translated.translation else ""

        header = _text(alert.header_text) if alert.HasField("header_text") else ""
        description = (
            _text(alert.description_text)
            if alert.HasField("description_text")
            else ""
        )

        affected = sorted(
            {
                e.route_id.upper()
                for e in alert.informed_entity
                if e.route_id and e.route_id.upper() in target_lines
            }
        )
        lines_str = ", ".join(affected) if affected else "unknown"

        active_str = _format_active_periods(alert)
        parts = [f"[Lines: {lines_str}] [{active_str}]"]
        if header:
            parts.append(header)
        if description:
            parts.append(description)
        results.append("\n".join(parts))

    if not results:
        lines_str = ", ".join(sorted(target_lines))
        dir_str = f" ({', '.join(directions)})" if directions else ""
        return f"No active alerts found for lines: {lines_str}{dir_str}."

    return f"Found {len(results)} alert(s):\n\n" + "\n\n---\n\n".join(results)


@app.tool(is_local=True)
def search_stops(
    query: Annotated[
        str,
        "Partial or full station name to search for, e.g. '14 St', 'Atlantic', 'Union Sq'",
    ],
) -> str:
    """
    Search for MTA subway stations by name. Returns matching station names and
    their GTFS stop IDs. Use this to find the stop_id needed for get_stop_arrivals.
    """
    q = query.strip().lower()
    matches = [
        (name, ids) for name, ids in _STOP_NAME_TO_IDS.items() if q in name.lower()
    ]
    if not matches:
        return f"No stations found matching '{query}'."
    matches.sort(key=lambda x: x[0])
    lines = []
    for name, ids in matches[:20]:
        entries = []
        for sid in ids:
            routes = _STOP_ID_TO_ROUTES.get(sid, [])
            routes_str = ", ".join(routes) if routes else "?"
            entries.append(f"{sid} (lines: {routes_str})")
        lines.append(f"  {name}: {' | '.join(entries)}")
    result = f"Found {len(matches)} station(s) matching '{query}':\n" + "\n".join(lines)
    if len(matches) > 20:
        result += f"\n  ... and {len(matches) - 20} more. Refine your query for fewer results."
    return result


@app.tool(is_local=True)
def get_stop_arrivals(
    stop_id: Annotated[
        str,
        "GTFS parent station ID from search_stops, e.g. 'A27'. "
        "Do not include a direction suffix (N/S) — the tool handles that.",
    ],
    stop_name: Annotated[
        str, "Human-readable station name for display, e.g. '14 St-8 Av'"
    ],
    line: Annotated[str, "Subway line identifier, e.g. 'A', '1', 'L'"],
    direction: Annotated[
        str,
        "Direction filter: 'north'/'uptown' or 'south'/'downtown'. "
        "Pass empty string for both directions.",
    ] = "",
    limit: Annotated[int, "Max number of upcoming arrivals to return. Defaults to 5."] = 5,
) -> str:
    """
    Fetch real-time arrival predictions for a subway stop and line.
    Use search_stops first to obtain the stop_id. Returns the next arrivals
    sorted by predicted arrival time.
    """
    line_upper = line.strip().upper()
    feed_path = _LINE_TO_FEED.get(line_upper)
    if not feed_path:
        return f"Unknown line '{line}'. Use letter/number designations like 'A', '1', 'L'."

    dir_id = _direction_ids([direction]) if direction.strip() else None
    if dir_id is None:
        target_stops = {f"{stop_id}N", f"{stop_id}S"}
    elif 1 in dir_id:
        target_stops = {f"{stop_id}N"}
    else:
        target_stops = {f"{stop_id}S"}

    try:
        response = httpx.get(f"{_MTA_FEED_BASE}{feed_path}", timeout=10)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return f"Failed to fetch MTA trip updates: {exc}"

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)

    now = time.time()
    arrivals: list[tuple[int, str]] = []  # (timestamp, directional_stop_id)

    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        if tu.trip.route_id.upper() != line_upper:
            continue
        for stu in tu.stop_time_update:
            if stu.stop_id not in target_stops:
                continue
            ts = stu.arrival.time if stu.arrival.time else stu.departure.time
            if ts and ts > now:
                arrivals.append((ts, stu.stop_id))

    if not arrivals:
        dir_str = f" ({direction})" if direction else ""
        return f"No upcoming {line_upper} train arrivals found at {stop_name}{dir_str}."

    arrivals.sort()
    lines_out: list[str] = []
    for ts, sid in arrivals[:limit]:
        dt = datetime.fromtimestamp(ts, tz=UTC).astimezone()
        mins = int((ts - now) / 60)
        dir_label = "northbound" if sid.endswith("N") else "southbound"
        lines_out.append(f"  {dt.strftime('%H:%M:%S')} ({mins} min) — {dir_label}")

    return f"{line_upper} train at {stop_name}:\n" + "\n".join(lines_out)


def _build_llm():
    if os.environ.get("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI

        openai_key = os.environ["OPENAI_API_KEY"]
        openai_base_url = os.environ.get("OPENAI_BASE_URL")
        if not openai_base_url:
            logger.info("OPENAI_BASE_URL unset - falling back to Grove default OpenAI base URL.")
            openai_base_url = "https://grove-gateway-prod.azure-api.net/grove-foundry-prod/openai/v1"
            
        kwargs: dict = {
            "api_key": openai_key,
            "model": os.environ.get("OPENAI_MODEL", "gpt-5.4-mini"),
        }
        if openai_base_url:
            if "grove-foundry" in openai_base_url:
                kwargs["base_url"] = openai_base_url.split("/v1")[0] + "/v1"
                kwargs["default_headers"] = {"api-key": openai_key}
            else:
                kwargs["base_url"] = openai_base_url.rstrip("/")
        return ChatOpenAI(**kwargs)
    elif os.environ.get("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic

        anthropic_key = os.environ["ANTHROPIC_API_KEY"]
        anthropic_base_url = os.environ.get("ANTHROPIC_BASE_URL")
        if not anthropic_base_url:
            logger.info("ANTHROPIC_BASE_URL unset - falling back to Grove default Anthropic base URL.")
            anthropic_base_url = "https://grove-gateway-prod.azure-api.net/grove-foundry-prod/anthropic/v1"
            
        kwargs: dict = {
            "api_key": anthropic_key,
            "model_name": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        }
        if anthropic_base_url:
            if "grove-foundry" in anthropic_base_url:
                kwargs["base_url"] = anthropic_base_url.split("/v1")[0].rstrip("/")
                kwargs["default_headers"] = {"api-key": anthropic_key}
            else:
                kwargs["base_url"] = anthropic_base_url.rstrip("/")
        return ChatAnthropic(**kwargs)
    elif os.environ.get("GEMINI_API_KEY"):
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
        )
    elif os.environ.get("CEREBRAS_API_KEY"):
        from langchain_cerebras import ChatCerebras

        return ChatCerebras(model=os.environ.get("CEREBRAS_MODEL", "llama3.1-8b"))
    else:
        raise Exception("No LLM provider configured.")


@app.entrypoint
def build_agent():
    llm = app.llm(_build_llm())
    tools = app.get_tools()
    bound_llm = llm.bind_tools(app.get_tool_schemas())

    system_prompt = SystemMessage(
        content=(
            "You are an MTA subway service assistant. "
            "When a user asks about subway alerts or service status, use the "
            "get_subway_alerts tool with the lines and directions they specify. "
            "Present the results clearly and concisely, including the active time windows. "
            "If the user doesn't specify a direction, pass an empty list for directions. "
            "If the user asks for currently active alerts or alerts in effect now, "
            "set active_only=True. "
            "Subway line identifiers use their letter/number designations "
            "(e.g. 'A', '1', 'L', 'N', 'Q'). "
            "Accepted directions: 'north'/'uptown' or 'south'/'downtown'. "
            "When a user asks about arrival times, next trains, or how long until the "
            "next train, first call search_stops with the station name or a partial name "
            "to find its stop_id, then call get_stop_arrivals with that stop_id. "
            "If search_stops returns exactly one station, proceed immediately to call "
            "get_stop_arrivals with that stop_id. "
            "If search_stops returns multiple candidate stations, ask the user to clarify "
            "which one they mean before calling get_stop_arrivals."
        )
    )

    def call_model(state: MessagesState):
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [system_prompt] + list(messages)
        response = bound_llm.invoke(messages)
        return {"messages": [response]}

    def should_continue(state: MessagesState):
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else END

    graph = StateGraph(MessagesState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", ToolNode(tools))
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue)
    graph.add_edge("tools", "agent")

    return graph.compile(checkpointer=app.checkpointer())
