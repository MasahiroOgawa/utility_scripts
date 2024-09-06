#!/bin/sh
# for azure VM (Standard NC4as T4 v3 (4 vcpus, 28 GiB memory)), Ubuntu22.04, set up script

echo "[INFO] install Nvidia driver"
sudo apt-get remove --purge '^nvidia-.*'
sudo apt-get remove --purge cuda-*
sudo add-apt-repository ppa:graphics-drivers/ppa
sudo apt update
sudo apt install nvidia-driver-535
reboot 


echo "[INFO] install docker"
#1. Set up Docker's apt repository.
# Add Docker's official GPG key:
sudo apt-get install ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
# Add the repository to Apt sources:
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
#2. Install the Docker packages.
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
#3. Verify that the Docker Engine installation is successful by running the hello-world image.
sudo docker run hello-world