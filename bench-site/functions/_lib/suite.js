// Written by make_suite_spec.py from the Hertzon Bench definition; do not edit.
export const SUITES = {
 "Hertzon Bench 1": {
  "tests": [
   {
    "id": "microstrip",
    "hashes": [
     "c12b4637790e5396"
    ],
    "time": 26.9,
    "tets": [
     4768,
     42918
    ]
   },
   {
    "id": "hairpin",
    "hashes": [
     "20a6d13167895682"
    ],
    "time": 82.23,
    "tets": [
     7524,
     67719
    ]
   },
   {
    "id": "patch",
    "hashes": [
     "c8eb2a957560cfee"
    ],
    "time": 208.05,
    "tets": [
     13421,
     120792
    ]
   },
   {
    "id": "patch_thin",
    "hashes": [
     "1548b63004871a64"
    ],
    "time": 135.69,
    "tets": [
     10462,
     94161
    ]
   },
   {
    "id": "waveguide",
    "hashes": [
     "a8885c744178ab43"
    ],
    "time": 56.71,
    "tets": [
     4343,
     39087
    ]
   },
   {
    "id": "sphere_rcs",
    "hashes": [
     "189c53257bd24fd0"
    ],
    "time": 26.38,
    "tets": [
     5337,
     48039
    ]
   }
  ],
  "titles": {
   "microstrip": "Microstrip line",
   "hairpin": "Hairpin filter",
   "patch": "Patch antenna",
   "patch_thin": "Patch on 0.1 mm",
   "waveguide": "Waveguide post",
   "sphere_rcs": "Sphere RCS"
  },
  "about": {
   "microstrip": "Two lumped ports, first order, 1-6 GHz sweep: start-up and small matrices",
   "hairpin": "Three resonators on 0.508 mm, second order, interpolating sweep",
   "patch": "Radiation boundary, far field, broadband 1.2-3.7 GHz sweep",
   "patch_thin": "Very thin substrate: the mesh of thin layers",
   "waveguide": "WR90 wave ports, 8.2-12.4 GHz sweep",
   "sphere_rcs": "Incident plane wave, monostatic RCS of a PEC sphere (checked against the exact Mie solution)"
  },
  "reference": "4-core cloud server (Intel Xeon 2.1 GHz, 16 GB), Linux, Hertzon 0.4.6"
 }
};
