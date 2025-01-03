__author__ = "Jumperkables"
"""
Generated by ChatGPT
"""
import os, sys
import ipdb
import matplotlib.pyplot as plt
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader
from torchvision import transforms
from torchvision.datasets import ImageFolder
import torchmetrics
import pytorch_lightning as pl
import wandb

#os.environ["CUDA_LAUNCH_BLOCKING"] = "1"
os.environ["PYTHONBREAKPOINT"] = "ipdb.set_trace"

DATA_DIR = f"data/isic_subset_cleaned"
NUM_CLASSES = len(os.listdir(DATA_DIR))
NUM_HEADS = 4
BATCH_SIZE = 16
VALTEST_BATCH_SIZE = 1
NUM_WORKERS = 4
ORTHOGONALITY_INTENSITY = 0.2
SALIENCY_INTENSITY = 1
SALIENCY_SIGMA = 1e-5
SALIENCY_ALPHA = 1e-5
USE_WANDB = True
LEARNING_RATE = 1e-5
N_PLOT_IMGS = 5
SALIENCY_MODE = "max" # "max" "mean"

class SkinLesionClassifier(pl.LightningModule):
    def __init__(self):
        super(SkinLesionClassifier, self).__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 64, kernel_size=3, stride=1, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2),
            nn.Conv2d(64, 128, kernel_size=3, stride=1, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2),
            nn.Conv2d(128, 256, kernel_size=3, stride=1, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2),
            nn.Conv2d(256, 512, kernel_size=3, stride=1, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2),
        )
        self.fc0 = nn.Sequential(
            nn.Linear(512 * 14 * 14, 1024),
            nn.ReLU(inplace=True),
            nn.Dropout(0.5)
        )
        for x in range(NUM_HEADS):
            head = nn.Sequential(
                nn.Linear(1024, 512),
                nn.ReLU(inplace=True),
                nn.Dropout(0.5)
            )
            cls_head = nn.Linear(512, NUM_CLASSES)
            setattr(self, f"head_{x}", head)
            setattr(self, f"cls_head_{x}", cls_head)
        self.criterion = nn.CrossEntropyLoss()
        self.train_acc = torchmetrics.Accuracy(task="multiclass", num_classes=NUM_CLASSES)
        self.valid_acc = torchmetrics.Accuracy(task="multiclass", num_classes=NUM_CLASSES)



    def forward(self, x):
        x = self.features(x)
        x = torch.flatten(x, 1)
        x = self.fc0(x)
        head_outputs = []
        cls_head_outputs = []
        for i in range(NUM_HEADS):
            h_out = getattr(self, f"head_{i}")(x)
            head_outputs.append(h_out)
            cls_h_out = getattr(self, f"cls_head_{i}")(h_out)
            cls_head_outputs.append(cls_h_out)
        head_outputs = torch.stack(head_outputs, dim=0)
        cls_head_outputs = torch.stack(cls_head_outputs, dim=0)
        return head_outputs, cls_head_outputs 



    def exec_step(self, mode, batch, batch_idx):
        images, labels = batch
        images.requires_grad_()
        head_outputs, cls_head_outputs = self(images)
        dot_prods = []  # Calculate vector similarity, and penalise to enforce orthogonality 
        saliencies = [] # Make sure that saliency maps are relatively concentrated
        for i in range(NUM_HEADS):
            if mode == "train": 
                cls_head_outputs[i].max(dim=1).values.sum().backward(retain_graph=True)
                grads = images.grad.data
                mean_mat = torch.ones(images.shape).to(images.device)*grads.mean()
                diffs = (grads-mean_mat).abs()
                sal = SALIENCY_INTENSITY*diffs.mean()
                sal = SALIENCY_ALPHA/((sal**1)+SALIENCY_SIGMA)
                saliencies.append(sal)
            else:
                saliencies.append(0.)
            for j in range(NUM_HEADS):
                if i != j:
                    dim_0 = head_outputs[i].shape[0]
                    dim_1 = head_outputs[i].shape[1]
                    dp = torch.bmm( head_outputs[i].view(dim_0, 1, dim_1), head_outputs[j].view(dim_0, dim_1, 1) )
                    dp = dp.squeeze(1).squeeze(1).abs()
                    dot_prods.append(dp)
        dot_prods = torch.stack(dot_prods, dim=0)
        preds = cls_head_outputs.mean(dim=0)
        head_loss = self.criterion(preds, labels) # Standard loss for class predictions
        orthog_loss = dot_prods.mean() # Penalise vector similarity, enforce orthogonality
        loss = head_loss + orthog_loss
        saliency_loss = sum(saliencies)
        loss = loss# + saliency_loss
        self.log(f"{mode}_loss_head", head_loss, prog_bar=(True if mode == "train" else False))
        self.log(f"{mode}_loss_orthog", orthog_loss, prog_bar=(True if mode == "train" else False))
        self.log(f"{mode}_loss_saliency", saliency_loss, prog_bar=(True if mode == "train" else False))
        self.log(f"{mode}_loss_total", loss, prog_bar=True)
        self.log(f"{mode}_acc", getattr(self, f"{mode}_acc")(preds, labels), prog_bar=True)
        return loss



    def training_step(self, batch, batch_idx):
        loss = self.exec_step("train", batch, batch_idx)
        return loss



    def validation_step(self, batch, batch_idx):
        loss = self.exec_step("valid", batch, batch_idx)
        if batch_idx in [i for i in range(N_PLOT_IMGS)]:
            self.plot_saliencies(batch, batch_idx)



    def plot_saliencies(self, batch, batch_idx):
        with torch.enable_grad():
            images, labels = batch
            images.requires_grad_()
            _, cls_head_outputs = self(images)
            bsz = images.shape[0]
            for i in range(NUM_HEADS):
                cls_head_outputs[i][0].max(dim=0).values.sum().backward(retain_graph=True)
                grads = images.grad.data[0]
                if SALIENCY_MODE == "max":
                    saliency = torch.max(grads, dim=0).values
                elif SALIENCY_MODE == "mean": 
                    saliency = torch.mean(grads, dim=0).values
                else:
                    raise ValueError(f"SALIENCY_MODE: '{SALIENCY_MODE}' should not be possible.")
                # Plot the image heatmap
                saliency = saliency.cpu().detach().numpy()
                fig, ax = plt.subplots(1,2)
                ax[0].imshow(images[0].cpu().detach().numpy().transpose(1, 2, 0))
                ax[0].axis('off')
                ax[1].imshow(saliency, cmap='hot')
                ax[1].axis('off')
                plt.tight_layout()
                fig.suptitle(f"Val Head-{i} Img-{batch_idx}")
                # Plot the image
                wandb.log({f"v_head-{i}_img-{batch_idx}": plt})
                plt.close()
                plt.cla()
                plt.clf()




    def configure_optimizers(self):
        return optim.Adam(self.parameters(), lr=LEARNING_RATE)



# Initialise wandb logger
if not USE_WANDB:
    os.environ["WANDB_MODE"] = "offline"
wandb.init(entity="jumperkables", project="ensemble_bias_reg")
wandb_logger = pl.loggers.WandbLogger()

# Set transforms for data augmentation
data_transforms = transforms.Compose([
    transforms.RandomResizedCrop(224),
    transforms.RandomHorizontalFlip(),
    transforms.ToTensor(),
    #transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
])

# Load the dataset
dataset = ImageFolder(DATA_DIR, transform=data_transforms)
train_size = int(0.8 * len(dataset))
test_size = len(dataset) - train_size
train_dataset, test_dataset = torch.utils.data.random_split(dataset, [train_size, test_size])

# Create data loaders
train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=NUM_WORKERS)
test_loader = DataLoader(test_dataset, batch_size=VALTEST_BATCH_SIZE, shuffle=False, num_workers=NUM_WORKERS)

# Initialize the Lightning module
skin_classifier = SkinLesionClassifier()

# Create a PyTorch Lightning trainer
trainer = pl.Trainer(logger=wandb_logger, inference_mode=False, max_epochs=1000, devices=1 if torch.cuda.is_available() else 0)#num_nodes=1, accelerator="gpu") #gpus=1 if torch.cuda.is_available() else 0)

# Train the model
trainer.fit(skin_classifier, train_loader, test_loader)
