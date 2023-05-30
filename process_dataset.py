import os, sys
import pandas as pd
import shutil
from tqdm import tqdm

ROOT_DIR = "data/isic_subset"
CLEANED_DIR = f"{ROOT_DIR}_cleaned"

md = pd.read_csv(f"{ROOT_DIR}/metadata.csv", usecols=["isic_id", "diagnosis"])
md = md.dropna()
unique_diagnoses = md.diagnosis.unique().tolist()
if not os.path.exists(CLEANED_DIR):
    os.mkdir(CLEANED_DIR)

# make folders for each class by name
for diag in unique_diagnoses:
    name = diag.replace(" ", "_")
    path_to_make = os.path.join(CLEANED_DIR, name)
    if os.path.exists(path_to_make):
        shutil.rmtree(path_to_make)
    os.mkdir(path_to_make)


for row in tqdm(md.iterrows(), total=len(md)):
    isic_id = row[1]["isic_id"]
    diagnosis = row[1]["diagnosis"]
    image_name = f"{isic_id}.JPG"
    save_path = os.path.join(CLEANED_DIR, diagnosis.replace(" ", "_"), image_name)
    shutil.copyfile(os.path.join(ROOT_DIR, image_name), save_path)
