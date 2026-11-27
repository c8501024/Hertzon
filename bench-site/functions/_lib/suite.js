// Written by make_suite_spec.py from the Hertzon Bench definition; do not edit.
export const SUITES = {
 "Hertzon Bench 1": {
  "tests": [
   {
    "id": "microstrip",
    "large": false,
    "hashes": [
     "c12b4637790e5396"
    ],
    "time": 26.9,
    "memory_gb": 2.0,
    "tets": [
     4768,
     42918
    ]
   },
   {
    "id": "hairpin",
    "large": false,
    "hashes": [
     "20a6d13167895682"
    ],
    "time": 82.23,
    "memory_gb": 5.3,
    "tets": [
     7524,
     67719
    ]
   },
   {
    "id": "patch",
    "large": false,
    "hashes": [
     "c8eb2a957560cfee"
    ],
    "time": 208.05,
    "memory_gb": 8.3,
    "tets": [
     13421,
     120792
    ]
   },
   {
    "id": "patch_thin",
    "large": false,
    "hashes": [
     "1548b63004871a64"
    ],
    "time": 135.69,
    "memory_gb": 6.2,
    "tets": [
     10462,
     94161
    ]
   },
   {
    "id": "waveguide",
    "large": false,
    "hashes": [
     "a8885c744178ab43"
    ],
    "time": 56.71,
    "memory_gb": 3.0,
    "tets": [
     4343,
     39087
    ]
   },
   {
    "id": "sphere_rcs",
    "large": false,
    "hashes": [
     "189c53257bd24fd0"
    ],
    "time": 26.38,
    "memory_gb": 3.1,
    "tets": [
     5337,
     48039
    ]
   },
   {
    "id": "dielectric_rcs",
    "large": false,
    "hashes": [
     "59f214cbca679ffb"
    ],
    "time": 25.64,
    "memory_gb": 3.0,
    "tets": [
     4555,
     40995
    ]
   },
   {
    "id": "drone_medium_rcs",
    "large": false,
    "hashes": [
     "f68e247311f2acd8"
    ],
    "time": 71.27,
    "memory_gb": 8.3,
    "tets": [
     15542,
     139878
    ]
   },
   {
    "id": "drone_rcs",
    "large": true,
    "hashes": [
     "eea3f4994da3d8de"
    ],
    "time": 281.78,
    "memory_gb": 36.2,
    "tets": [
     61157,
     550413
    ]
   },
   {
    "id": "rotman_array",
    "large": true,
    "hashes": [
     "cb52b197c82fdb9d"
    ],
    "time": 445.88,
    "memory_gb": 25.9,
    "tets": [
     48081,
     432732
    ]
   }
  ],
  "scaling_test": "hairpin",
  "scaling_tests": {
   "rf": "hairpin",
   "rcs": "sphere_rcs"
  },
  "single_core": {
   "hairpin": 207.0,
   "sphere_rcs": 83.73
  },
  "titles": {
   "microstrip": "Microstrip line",
   "hairpin": "Hairpin filter",
   "patch": "Patch antenna",
   "patch_thin": "Patch on 0.1 mm",
   "waveguide": "Waveguide post",
   "sphere_rcs": "Sphere RCS",
   "dielectric_rcs": "Dielectric sphere RCS",
   "drone_medium_rcs": "Drone RCS (medium)",
   "drone_rcs": "Drone RCS (workstation)",
   "rotman_array": "Rotman lens array (workstation)"
  },
  "about": {
   "microstrip": "Two lumped ports, first order, 1-6 GHz sweep: start-up and small matrices",
   "hairpin": "Three resonators on 0.508 mm, second order, interpolating sweep",
   "patch": "Radiation boundary, far field, broadband 1.2-3.7 GHz sweep",
   "patch_thin": "Very thin substrate: the mesh of thin layers",
   "waveguide": "WR90 wave ports, 8.2-12.4 GHz sweep",
   "sphere_rcs": "Incident plane wave, monostatic RCS of a PEC sphere (checked against the exact Mie solution)",
   "dielectric_rcs": "Incident plane wave, monostatic RCS of a lossless dielectric sphere (er = 4), fields inside and outside (checked against the exact Mie solution)",
   "drone_medium_rcs": "Imported 3D surface of a delta-wing drone, PEC, incident plane wave at 0.5 GHz, monostatic RCS: a realistic model that fits a computer with 16 GB",
   "drone_rcs": "Imported 3D surface of a delta-wing drone, PEC, incident plane wave, monostatic RCS: large meshes",
   "rotman_array": "24 GHz Rotman lens beamformer with its patch array on RO4350B, 11 lumped ports, second order, far field and a discrete sweep at 22, 24 and 26 GHz: a large antenna system"
  },
  "reference": "4-core cloud server (Intel Xeon 2.1 GHz, 16 GB), Linux, Hertzon 0.4.6",
  "reference_large": "AMD Ryzen Threadripper 3970X (32 cores, 64 threads used, 256 GB), Windows, Hertzon 0.4.6",
  "parts": [
   {
    "id": "rf",
    "title": "RF",
    "tests": [
     "microstrip",
     "hairpin",
     "patch",
     "patch_thin",
     "waveguide"
    ]
   },
   {
    "id": "rcs",
    "title": "RCS",
    "tests": [
     "sphere_rcs",
     "dielectric_rcs",
     "drone_medium_rcs"
    ]
   }
  ]
 }
};
