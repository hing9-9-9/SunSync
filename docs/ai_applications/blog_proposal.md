# ITE 351: AI & Applications — Project Proposal Blog

> **Course:** ITE 351: AI & Applications  
> **Topic Track:** Option B: Prototype Service using AIML Techniques (AIML + LG Appliance Ecosystem)  
> **Project Repository:** [https://github.com/hing9-9-9/SunSync](https://github.com/hing9-9-9/SunSync)  
> **Date:** November 2026  

---

## Task 1: Report Group Members

* **Jiyeon Kim**, Dept. of Information Systems, Hanyang University, kjy8402@hanyang.ac.kr
* **Daeun Lee**, Dept. of Information Systems, Hanyang University, daeun4862@hanyang.ac.kr
* **Dain Lee**, Dept. of Information Systems, Hanyang University, ldi0308@hanyang.ac.kr
* **Heegyeong Chung**, Dept. of Information Systems, Hanyang University, hgchung04@gmail.com
* **Misong Ha**, Dept. of Information Systems, Hanyang University, msha9865@hanyang.ac.kr

---

## Task 3: Research Idea (< 200 words)

### Title
**SunSync-AI: Machine Learning-Driven Solar Nowcasting and Computer Vision for Proactive Smart Film Shading Control**

### Proposal
Libraries, museums, and smart homes with large windows face a recurring trade-off between preserving light-sensitive items from irreversible photodegradation and maintaining natural daylight. Existing motorized blinds and dynamic glass react only after glare occurs or darken entire panes without knowing where sensitive objects are located. Inspired by LG Chem’s switchable glazing film and LG ThinQ ON smart-home ecosystem (Option B), we propose **SunSync-AI**, an intelligent service that proactively switches only the specific $10 \times 10$ window film cells whose sunlight rays would reach protected targets. To advance our software engineering prototype into an AI-driven system, we investigate three AIML applications: (1) **Sub-Hourly Solar Irradiance Nowcasting**, training regression and sequential models (Scikit-learn, LightGBM, PyTorch LSTM) on Open-Meteo and solar radiation datasets to predict short-term Direct Normal Irradiance (DNI) and cloud-induced fluctuations; (2) **Vision-Based 3D Indoor Sensing**, combining marker-based camera pose estimation with deep object detection and monocular depth estimation (YOLO, Depth Anything) to automatically classify and measure indoor furniture from smartphone photos; and (3) **Uncertainty-Aware Switching Optimization**, learning adaptive hysteresis thresholds from forecast residuals to eliminate film flickering while maximizing daylight. *(185 words including title)*

---

## Technical Architecture & Development Steps

While the core **SunSync** project provides deterministic 3D geometry and space management, **SunSync-AI** introduces empirical machine learning and computer vision pipelines across three key development steps:

```text
+-----------------------------------------------------------------------------------+
|                            SunSync-AI Pipeline Flow                               |
+-----------------------------------------------------------------------------------+
|  1. Vision AI (Mobile / FR-03 & FR-06)                                            |
|     Smartphone Photo (Window ArUco Marker + Room)                                 |
|       ==> ArUco solvePnP Metric Pose + YOLOv8 Furniture Detection & Depth Fusion  |
|       ==> Auto-Classified 3D Bounding Boxes (Bookshelves, Desks, Protected Zones) |
+-----------------------------------------------------------------------------------+
                                      │
                                      ▼
+-----------------------------------------------------------------------------------+
|  2. Solar ML Nowcasting & Data Analysis (Backend / FR-01.4)                       |
|     Open-Meteo Hourly Forecasts + Historical Solar Radiation Datasets            |
|       ==> Feature Engineering (Clear-Sky Index, Sun Elevation, Lagged DNI, Clouds)|
|       ==> ML Nowcaster (Scikit-learn, LightGBM Quantile Regression, PyTorch LSTM)  |
|       ==> 15-Minute Sub-Hourly DNI Predictions with Uncertainty Bounds (P10 - P90) |
+-----------------------------------------------------------------------------------+
                                      │
                                      ▼
+-----------------------------------------------------------------------------------+
|  3. Uncertainty-Aware Intelligent Shading Control (Engine / FR-02 & FR-04)        |
|     Deterministic 3D Ray Tracing (Tile Path -> Window Crossing)                   |
|       + Predicted DNI Quantiles & Exceedance Probability                          |
|       ==> Multi-Objective Cost Optimization (Preservation vs Daylight vs Flicker)|
|       ==> Target-Specific 10x10 Film Cell Switching Commands                      |
|       ==> Integrated with LG Chem Switchable Glazing Film & LG ThinQ ON Ecosystem |
+-----------------------------------------------------------------------------------+
```

---

### Step 1: Solar Irradiance Data Analysis & Sub-Hourly Nowcasting (Option A Step)
* **Dataset Investigation:** We investigate solar irradiance records from Open-Meteo Forecast/Historical APIs alongside physical solar radiation datasets (e.g., NSRDB / KMA solar records).
* **Feature Engineering:** We construct domain-specific features, including clear-sky index ($k_t = \text{DNI} / \text{DNI}_{\text{clear}}$), solar elevation and azimuth angles, relative humidity, cloud cover stratification (low, mid, high), and lagged temporal deltas.
* **Model Benchmark:**
  1. *Baseline:* ElasticNet & Ridge Regression (`scikit-learn`)
  2. *Tree Ensembles:* LightGBM & XGBoost with Quantile Loss to generate probabilistic prediction intervals ($\alpha = 0.1, 0.5, 0.9$)
  3. *Deep Sequence Model:* 1D-CNN / LSTM (`PyTorch`) for short-term temporal sequence forecasting

---

### Step 2: Computer Vision for Marker-Anchored 3D Spatial Sensing
* **Automated Furniture Categorization:** Using transfer learning on lightweight object detection models (YOLOv8 nano/small), user photos taken from reading rooms are classified into categories (`bookshelf`, `desk`, `chair`, `other`) to remove manual tagging overhead.
* **Metric Depth & Pose Fusion:** The $6 \times 6$ window-film ArUco marker provides an exact scale and camera pose matrix ($T_{\text{ENU}}^{\text{marker}}$ via OpenCV `solvePnP`). Monocular depth estimation models (Depth Anything V2 / MiDaS) predict pixel-level relative depths, which are calibrated against the known metric marker dimensions to recover 3D bounding boxes with minimal user movement.

---

### Step 3: Prototype Service Integration with Smart Home Ecosystem (Option B)
* **Adaptive Control Engine:** Instead of fixed rule-based thresholds, the control engine balances three competing objectives: minimizing light damage on Grade A/B/C materials, maximizing room illuminance, and minimizing film state switching frequency under cloud uncertainty.
* **LG Smart Appliance Synergy:** Designed to interface with **LG Chem Switchable Glazing Film (SGF)** and **LG ThinQ ON** AI Home Hub routines via standard protocols (Matter / REST APIs), providing an intelligent proactive shading appliance for next-generation smart buildings.
