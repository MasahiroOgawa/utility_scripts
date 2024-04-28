#/bin/bash
# Explanation: This is a total install script of CUDA12.3 and related libraries to Ubuntu20.04 
# Author: Masahiro Ogawa
# Reference:
#   whole procedure: https://qiita.com/cinchan/items/9718e1f26146dc5e3eaa
#   cuda GPG key: https://developer.nvidia.com/blog/updating-the-cuda-linux-gpg-repository-key/#:~:text=If%20you%20can't%20install%20the%20cuda%2Dkeyring%20package%2C,WSL%20$%20sudo%20apt%2Dkey%20adv%20%2D%2Dfetch%2Dkeys%20
#   driver: https://developer.nvidia.com/cuda-downloads?target_os=Linux&target_arch=x86_64&Distribution=Ubuntu&target_version=20.04&target_type=deb_local
#   cudnn: https://developer.nvidia.com/cudnn-downloads?target_os=Linux&target_arch=x86_64&Distribution=Ubuntu&target_version=20.04&target_type=deb_local
#   tips: https://qiita.com/Manyan3/items/628b3b22700fd569e8fb
###
set -e

echo "[INFO] remove existing cuda to avoid compatible issues..."
sudo apt remove --purge "nvidia-*" -y && sudo apt autoremove -y
sudo apt remove --purge "cuda-*" -y && sudo apt autoremove -y
sudo apt remove --purge "cudnn-*" -y && sudo apt autoremove -y
sudo apt remove --purge "libcudnn*" -y && sudo apt autoremove -y
sudo apt remove --purge "libnvidia-*" -y && sudo apt autoremove -y
echo "[INFO] done removal."

echo "[INFO] update cuda GPG key..."
sudo apt-key del 7fa2af80
DEB_FILE=cuda-keyring_1.0-1_all.deb
if [ ! -f ${DEB_FILE} ]; then
    wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2004/x86_64/cuda-keyring_1.0-1_all.deb
fi
sudo dpkg -i cuda-keyring_1.0-1_all.deb
echo "[INFO] done updating GPG key."

echo "[INFO] install Nvidia driver"
PIN_FILE=cuda-ubuntu2004.pin
if [ ! -f ${PIN_FILE} ]; then
    wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2004/x86_64/${PIN_FILE}
fi
sudo mv ${PIN_FILE} /etc/apt/preferences.d/cuda-repository-pin-600
DEB_FILE=cuda-repo-ubuntu2004-12-4-local_12.4.1-550.54.15-1_amd64.deb
if [ ! -f ${DEB_FILE} ]; then
    wget https://developer.download.nvidia.com/compute/cuda/12.4.1/local_installers/${DEB_FILE}
fi
sudo dpkg -i ${DEB_FILE}
sudo cp /var/cuda-repo-ubuntu2004-12-4-local/cuda-*-keyring.gpg /usr/share/keyrings/
# sudo rm -f /etc/apt/sources.list.d/cuda.list
# sudo rm -f /etc/apt/sources.list.d/nvidia-ml.list
sudo apt-get update
sudo apt-get -y install cuda-toolkit-12-4
sudo apt-get -y install nvidia-driver-550-open
sudo apt-get -y install cuda-drivers-550
echo "[INFO] done driver installation."

echo "[INFO] install cudnn..."
DEB_FILE=cudnn-local-repo-ubuntu2004-9.1.0_1.0-1_amd64.deb
if [ ! -f ${DEB_FILE} ]; then
   wget https://developer.download.nvidia.com/compute/cudnn/9.1.0/local_installers/${DEB_FILE}
fi
sudo dpkg -i ${DEB_FILE}
sudo cp /var/cudnn-local-repo-ubuntu2004-9.1.0/cudnn-*-keyring.gpg /usr/share/keyrings/
sudo apt-get update
sudo apt-get -y install cudnn
sudo apt-get -y install cudnn-cuda-12
echo "[INFO] done cudnn installation."

echo "[INFO] clean up downloaded files..."
rm -f *.deb*
echo "[INFO] done. please reboot."

