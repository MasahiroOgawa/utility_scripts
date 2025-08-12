import argparse
import glob
import os
import shutil
import subprocess
import sys

import requests


def download_segments_from_m3u8(m3u8_file_path, output_directory, base_url=None):
    """
    Download video segments from M3U8 file (any URL, not just .ts/.jpeg)
    Supports optional base_url for relative segment paths.

    Args:
        m3u8_file_path: Path to the M3U8 file
        output_directory: Directory to save downloaded files
        base_url: Optional base URL for relative segment paths

    Returns:
        bool: True if download completed successfully, False otherwise
    """
    if not os.path.exists(m3u8_file_path):
        print(f"Error: M3U8 file '{m3u8_file_path}' not found.")
        return False

    os.makedirs(output_directory, exist_ok=True)

    with open(m3u8_file_path, "r") as f:
        lines = f.readlines()

    segment_urls = []
    for line in lines:
        line = line.strip()
        # Ignore comments and empty lines
        if not line or line.startswith("#"):
            continue
        # Accept any line that looks like a segment URL or path
        if line.startswith("http://") or line.startswith("https://"):
            segment_urls.append(line)
        elif base_url:
            # Join base_url and relative path
            segment_urls.append(base_url.rstrip("/") + "/" + line.lstrip("/"))
        else:
            print(f"Warning: Skipping relative segment path without base_url: {line}")

    print(f"Found {len(segment_urls)} segment URLs in M3U8.")

    download_errors = 0
    skipped_files = 0
    for i, url in enumerate(segment_urls):
        filename = os.path.join(output_directory, f"segment{i:04d}.ts")

        # Skip download if file already exists
        if os.path.exists(filename):
            print(f"File {filename} already exists, skipping download...")
            skipped_files += 1
            continue

        try:
            print(f"Downloading {url} to {filename}...")
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.eporner.com/",  # Set to main site or playlist page
            }
            response = requests.get(url, stream=True, headers=headers)
            response.raise_for_status()

            with open(filename, "wb") as out_file:
                for chunk in response.iter_content(chunk_size=8192):
                    out_file.write(chunk)
            print(f"Downloaded {filename}")
        except requests.exceptions.RequestException as e:
            print(f"Error downloading {url}: {e}")
            print("Skipping to next file.")
            download_errors += 1
        except Exception as e:
            print(f"An unexpected error occurred for {url}: {e}")
            print("Skipping to next file.")
            download_errors += 1

    if download_errors > 0:
        print(f"Download complete with {download_errors} errors.")
    else:
        print("Download complete successfully.")

    if skipped_files > 0:
        print(f"Skipped {skipped_files} files that already existed.")

    return True


def validate_and_fix_jpegs(input_dir):
    """
    Validate JPEG files and remove/fix corrupted ones (optimized for large file counts)

    Args:
        input_dir: Directory containing JPEG files

    Returns:
        tuple: (valid_count, removed_count)
    """
    jpeg_pattern = os.path.join(input_dir, "video*.jpeg")
    jpeg_files = sorted(glob.glob(jpeg_pattern))

    valid_count = 0
    removed_count = 0
    total_files = len(jpeg_files)

    print(f"Validating {total_files} JPEG files...")

    for i, jpeg_file in enumerate(jpeg_files):
        # Progress indicator for large file counts
        if i % 100 == 0:
            print(f"Progress: {i}/{total_files} files processed...")

        is_valid = True

        try:
            # Check file size first - very small files are likely corrupted
            file_size = os.path.getsize(jpeg_file)
            if file_size < 512:  # Less than 512 bytes is definitely corrupted
                print(
                    f"Removing tiny file: {os.path.basename(jpeg_file)} ({file_size} bytes)"
                )
                os.remove(jpeg_file)
                removed_count += 1
                continue

            # Quick JPEG magic bytes check
            with open(jpeg_file, "rb") as f:
                header = f.read(4)
                if not header.startswith(b"\xff\xd8\xff"):
                    print(
                        f"Removing file with invalid JPEG header: {os.path.basename(jpeg_file)}"
                    )
                    os.remove(jpeg_file)
                    removed_count += 1
                    continue

                # Check for JPEG end marker at the end of file
                f.seek(-2, 2)  # Go to last 2 bytes
                footer = f.read(2)
                if footer != b"\xff\xd9":
                    print(
                        f"Removing incomplete JPEG file: {os.path.basename(jpeg_file)}"
                    )
                    os.remove(jpeg_file)
                    removed_count += 1
                    continue

            # Light validation - just check file headers without PIL
            try:
                # Just check if we can read the file as binary
                with open(jpeg_file, "rb") as f:
                    # Read enough bytes to check basic structure
                    data = f.read(1024)  # Read first 1KB
                    if len(data) > 500:  # Basic size check
                        is_valid = True
            except Exception:
                print(f"Removing unreadable file: {os.path.basename(jpeg_file)}")
                is_valid = False

            if is_valid:
                valid_count += 1

        except Exception as e:
            print(f"Removing problematic file: {os.path.basename(jpeg_file)} - {e}")
            is_valid = False

        if not is_valid:
            try:
                os.remove(jpeg_file)
                removed_count += 1
            except OSError:
                pass  # File might already be removed

    print(f"Validation complete: {valid_count} valid, {removed_count} removed")
    return valid_count, removed_count


def ffmpeg_validate_jpegs(input_dir):
    """
    Use ffmpeg to validate JPEG files - more thorough than PIL (optimized for large file counts)

    Args:
        input_dir: Directory containing JPEG files

    Returns:
        tuple: (valid_count, removed_count)
    """
    jpeg_pattern = os.path.join(input_dir, "video*.jpeg")
    jpeg_files = sorted(glob.glob(jpeg_pattern))

    valid_count = 0
    removed_count = 0
    total_files = len(jpeg_files)

    print(f"FFmpeg validating {total_files} JPEG files...")

    for i, jpeg_file in enumerate(jpeg_files):
        # Progress indicator for large file counts
        if i % 50 == 0:
            print(f"FFmpeg validation progress: {i}/{total_files} files processed...")

        try:
            # Use ffmpeg to test decode the JPEG file
            cmd = [
                "ffmpeg",
                "-v",
                "error",  # Only show errors
                "-i",
                jpeg_file,
                "-f",
                "null",
                "-",
            ]

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5
            )  # 5 second timeout per file

            if result.returncode == 0:
                valid_count += 1
            else:
                print(
                    f"Removing JPEG file that ffmpeg cannot decode: {os.path.basename(jpeg_file)}"
                )
                if result.stderr.strip():
                    print(
                        f"FFmpeg error: {result.stderr.strip()[:100]}..."
                    )  # Truncate long error messages
                os.remove(jpeg_file)
                removed_count += 1

        except subprocess.TimeoutExpired:
            print(
                f"Removing JPEG file that timed out during validation: {os.path.basename(jpeg_file)}"
            )
            try:
                os.remove(jpeg_file)
                removed_count += 1
            except OSError:
                pass
        except Exception as e:
            print(f"Error validating {os.path.basename(jpeg_file)} with ffmpeg: {e}")
            try:
                os.remove(jpeg_file)
                removed_count += 1
            except OSError:
                pass

    print(f"FFmpeg validation complete: {valid_count} valid, {removed_count} removed")
    return valid_count, removed_count


def combine_jpegs_to_mp4(input_dir, output_file="output_video.mp4", framerate=30):
    """
    Combine JPEG files into MP4 video using ffmpeg - optimized for VLC-compatible JPEGs

    Args:
        input_dir: Directory containing JPEG files
        output_file: Output MP4 filename
        framerate: Video framerate (default: 30)

    Returns:
        bool: True if combination successful, False otherwise
    """
    # Check if ffmpeg is available
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: ffmpeg is not installed or not found in PATH")
        return False

    jpeg_pattern = os.path.join(input_dir, "video*.jpeg")
    jpeg_files = sorted(glob.glob(jpeg_pattern))

    if not jpeg_files:
        print(f"No JPEG files found in {input_dir}")
        return False

    print(f"Found {len(jpeg_files)} JPEG files to combine")
    input_pattern = os.path.join(input_dir, "video%04d.jpeg")

    # Strategy 1: Use image2 demuxer with more lenient settings
    print("Trying image2 demuxer approach...")
    ffmpeg_cmd_image2 = [
        "ffmpeg",
        "-y",
        "-f",
        "image2",
        "-framerate",
        str(framerate),
        "-i",
        input_pattern,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",  # Ensure even dimensions
        "-movflags",
        "+faststart",
        output_file,
    ]

    try:
        result = subprocess.run(
            ffmpeg_cmd_image2, capture_output=True, text=True, timeout=300
        )

        if (
            result.returncode == 0
            and os.path.exists(output_file)
            and os.path.getsize(output_file) > 1024
        ):
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            print(f"Successfully created {output_file} using image2 demuxer")
            print(f"Output video size: {size_mb:.1f} MB")
            return True
        else:
            print(f"Image2 approach failed: {result.stderr[:300]}...")

    except subprocess.TimeoutExpired:
        print("Image2 approach timed out")
    except Exception as e:
        print(f"Image2 approach error: {e}")

    # Strategy 2: Use concat demuxer (create file list)
    print("Trying concat demuxer approach...")
    try:
        concat_file = os.path.join(input_dir, "file_list.txt")
        with open(concat_file, "w") as f:
            for jpeg_file in jpeg_files:
                rel_path = os.path.relpath(jpeg_file, input_dir)
                f.write(f"file '{rel_path}'\n")
                f.write(f"duration {1.0 / framerate}\n")

        ffmpeg_cmd_concat = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-movflags",
            "+faststart",
            output_file,
        ]

        result = subprocess.run(
            ffmpeg_cmd_concat, capture_output=True, text=True, timeout=300
        )

        # Clean up
        try:
            os.remove(concat_file)
        except Exception as e:
            print(f"Warning: Could not remove concat file: {e}")

        if (
            result.returncode == 0
            and os.path.exists(output_file)
            and os.path.getsize(output_file) > 1024
        ):
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            print(f"Successfully created {output_file} using concat demuxer")
            print(f"Output video size: {size_mb:.1f} MB")
            return True
        else:
            print(f"Concat approach failed: {result.stderr[:300]}...")

    except subprocess.TimeoutExpired:
        print("Concat approach timed out")
    except Exception as e:
        print(f"Concat approach error: {e}")

    # Strategy 3: Copy individual files to temporary location and use glob pattern
    print("Trying copy-and-process approach...")
    try:
        temp_dir = os.path.join(input_dir, "temp_processing")
        os.makedirs(temp_dir, exist_ok=True)

        print("Copying files to temporary location...")
        copied_files = []
        for i, jpeg_file in enumerate(
            jpeg_files[:100]
        ):  # Process first 100 files as test
            if i % 50 == 0:
                print(f"Copying file {i}/{min(100, len(jpeg_files))}...")
            temp_name = os.path.join(temp_dir, f"frame{i:06d}.jpg")
            shutil.copy2(jpeg_file, temp_name)
            copied_files.append(temp_name)

        temp_pattern = os.path.join(temp_dir, "frame%06d.jpg")

        ffmpeg_cmd_temp = [
            "ffmpeg",
            "-y",
            "-f",
            "image2",
            "-framerate",
            str(framerate),
            "-i",
            temp_pattern,
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "25",
            "-pix_fmt",
            "yuv420p",
            "-t",
            "10",  # Create 10 second test video
            os.path.join(input_dir, "test_output.mp4"),
        ]

        result = subprocess.run(
            ffmpeg_cmd_temp, capture_output=True, text=True, timeout=60
        )

        # Clean up temp files
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass

        test_output = os.path.join(input_dir, "test_output.mp4")
        if (
            result.returncode == 0
            and os.path.exists(test_output)
            and os.path.getsize(test_output) > 1024
        ):
            print("Test successful! Processing all files...")

            # Now process all files
            os.makedirs(temp_dir, exist_ok=True)
            for i, jpeg_file in enumerate(jpeg_files):
                if i % 200 == 0:
                    print(f"Processing file {i}/{len(jpeg_files)}...")
                temp_name = os.path.join(temp_dir, f"frame{i:06d}.jpg")
                shutil.copy2(jpeg_file, temp_name)

            temp_pattern = os.path.join(temp_dir, "frame%06d.jpg")

            ffmpeg_cmd_full = [
                "ffmpeg",
                "-y",
                "-f",
                "image2",
                "-framerate",
                str(framerate),
                "-i",
                temp_pattern,
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                output_file,
            ]

            print("Creating full video...")
            result = subprocess.run(ffmpeg_cmd_full, capture_output=True, text=True)

            # Clean up
            try:
                shutil.rmtree(temp_dir)
                os.remove(test_output)
            except Exception:
                pass

            if (
                result.returncode == 0
                and os.path.exists(output_file)
                and os.path.getsize(output_file) > 1024
            ):
                size_mb = os.path.getsize(output_file) / (1024 * 1024)
                print(f"Successfully created {output_file} using copy approach")
                print(f"Output video size: {size_mb:.1f} MB")
                return True
        else:
            print(f"Copy approach test failed: {result.stderr[:300]}...")

    except Exception as e:
        print(f"Copy approach error: {e}")

    # Strategy 4: Force with maximum error tolerance
    print("Trying maximum error tolerance approach...")
    ffmpeg_cmd_force = [
        "ffmpeg",
        "-y",
        "-analyzeduration",
        "2147483647",
        "-probesize",
        "2147483647",
        "-f",
        "image2",
        "-framerate",
        str(framerate),
        "-i",
        input_pattern,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        "-pix_fmt",
        "yuv420p",
        "-avoid_negative_ts",
        "make_zero",
        "-fflags",
        "+discardcorrupt+genpts+igndts",
        "-err_detect",
        "ignore_err",
        "-ignore_unknown",
        "-max_muxing_queue_size",
        "4096",
        output_file,
    ]

    try:
        result = subprocess.run(ffmpeg_cmd_force, capture_output=True, text=True)

        if os.path.exists(output_file) and os.path.getsize(output_file) > 1024:
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            print(f"Force approach created output: {size_mb:.1f} MB")
            if result.returncode != 0:
                print("Warning: Video created with errors, but should be playable")
            return True
        else:
            print("Force approach failed to create usable output")

    except Exception as e:
        print(f"Force approach error: {e}")

    print(
        "All approaches failed. The JPEG files may use a format that's incompatible with ffmpeg's MJPEG decoder."
    )
    print(
        "You might need to convert the files to a different format first or use a different tool."
    )
    return False


def try_alternative_ffmpeg_approach(input_dir, output_file, framerate):
    """
    Alternative approach using concat demuxer to handle corrupted files better
    """
    try:
        # Get list of valid JPEG files
        jpeg_pattern = os.path.join(input_dir, "video*.jpeg")
        jpeg_files = sorted(glob.glob(jpeg_pattern))

        if not jpeg_files:
            return False

        # Create a temporary file list for ffmpeg concat
        concat_file = os.path.join(input_dir, "file_list.txt")
        with open(concat_file, "w") as f:
            for jpeg_file in jpeg_files:
                # Use relative path from input_dir
                rel_path = os.path.relpath(jpeg_file, input_dir)
                f.write(f"file '{rel_path}'\n")
                f.write(f"duration {1.0 / framerate}\n")  # Set frame duration

        # FFmpeg command using concat demuxer
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(framerate),  # Set output framerate
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",  # Ensure even dimensions
            output_file,
        ]

        print("Trying concat approach...")
        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)

        # Clean up temporary file
        try:
            os.remove(concat_file)
        except Exception as e:
            print(f"Warning: Could not remove concat file: {e}")
            pass

        if result.returncode == 0:
            print(f"Successfully created {output_file} using alternative approach")
            return True
        else:
            print(f"Alternative approach also failed: {result.stderr}")
            return False

    except Exception as e:
        print(f"Alternative approach error: {e}")
        return False


def force_combine_jpegs_to_mp4(input_dir, output_file="output_video.mp4", framerate=30):
    """
    Force combine JPEG files without validation - fastest approach
    """
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: ffmpeg is not installed or not found in PATH")
        return False

    jpeg_pattern = os.path.join(input_dir, "video*.jpeg")
    jpeg_files = sorted(glob.glob(jpeg_pattern))

    if not jpeg_files:
        print(f"No JPEG files found in {input_dir}")
        return False

    print(f"Force combining {len(jpeg_files)} JPEG files without validation...")

    input_pattern = os.path.join(input_dir, "video%04d.jpeg")

    # Ultra-permissive ffmpeg command
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(framerate),
        "-pattern_type",
        "sequence",
        "-i",
        input_pattern,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        "-pix_fmt",
        "yuv420p",
        "-f",
        "mp4",
        "-ignore_unknown",
        "-err_detect",
        "ignore_err",
        "-fflags",
        "+discardcorrupt+genpts+igndts",
        "-avoid_negative_ts",
        "make_zero",
        "-vsync",
        "vfr",
        "-max_muxing_queue_size",
        "2048",
        output_file,
    ]

    try:
        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)

        # Accept any output, even with errors
        if os.path.exists(output_file) and os.path.getsize(output_file) > 1024:
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            print(f"Force combine created output: {size_mb:.1f} MB")
            if result.returncode != 0:
                print("Warning: Video created with errors, check quality manually")
            return True
        else:
            print("Force combine failed to create usable output")
            return False

    except Exception as e:
        print(f"Force combine error: {e}")
        return False


def combine_mpegts_segments_to_mp4(input_dir, output_file="output_video.mp4"):
    """
    Combine MPEGTS video segments into MP4 video using ffmpeg with multiple strategies

    Args:
        input_dir: Directory containing segment files (.ts)
        output_file: Output MP4 filename

    Returns:
        bool: True if combination successful, False otherwise
    """
    # Check if ffmpeg is available
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: ffmpeg is not installed or not found in PATH")
        return False

    # Find all .ts files in order
    ts_files = sorted(glob.glob(os.path.join(input_dir, "segment*.ts")))

    if not ts_files:
        print(f"No segment files (.ts) found in {input_dir}")
        return False

    print(f"Found {len(ts_files)} segment files to combine")
    
    # Check if files are valid MPEG-TS format
    print("Checking file format...")
    try:
        with open(ts_files[0], "rb") as f:
            header = f.read(4)
            # Check for MPEG-TS sync byte (0x47)
            if header[0] != 0x47 and header[1] != 0x47 and header[2] != 0x47 and header[3] != 0x47:
                # Check if it might be encrypted
                print("\n⚠️  WARNING: The .ts files do not appear to be valid MPEG-TS format.")
                print("Possible reasons:")
                print("1. The files may be encrypted (HLS encryption)")
                print("2. The files may be corrupted during download")
                print("3. The files may be in a different format despite the .ts extension")
                print("\nAttempting to combine anyway, but this may fail...")
    except Exception as e:
        print(f"Warning: Could not check file format: {e}")
    
    # Strategy 1: Try concat demuxer with copy codec (fastest)
    print("Strategy 1: Trying concat demuxer with stream copy...")
    try:
        concat_file = os.path.join(input_dir, "segment_list.txt")
        with open(concat_file, "w") as f:
            for ts_file in ts_files:
                abs_path = os.path.abspath(ts_file)
                f.write(f"file '{abs_path}'\n")
        
        ffmpeg_cmd_concat = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file,
            "-c",
            "copy",
            "-bsf:a",
            "aac_adtstoasc",
            output_file,
        ]
        
        print("Running ffmpeg concatenation (this may take a while for large files)...")
        result = subprocess.run(
            ffmpeg_cmd_concat, capture_output=True, text=True, timeout=1800
        )
        
        try:
            os.remove(concat_file)
        except Exception:
            pass
        
        if (
            result.returncode == 0
            and os.path.exists(output_file)
            and os.path.getsize(output_file) > 1024
        ):
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            print(f"Successfully created {output_file} using concat demuxer")
            print(f"Output video size: {size_mb:.1f} MB")
            return True
        else:
            print(f"Strategy 1 failed: {result.stderr[:300] if result.stderr else 'Unknown error'}...")
    except subprocess.TimeoutExpired:
        print("Strategy 1 timed out")
    except Exception as e:
        print(f"Strategy 1 error: {e}")

    # Strategy 2: Try concat protocol (direct concatenation)
    print("\nStrategy 2: Trying concat protocol...")
    try:
        # Build concat string
        concat_string = "concat:" + "|".join([os.path.abspath(f) for f in ts_files])
        
        ffmpeg_cmd_protocol = [
            "ffmpeg",
            "-y",
            "-i",
            concat_string,
            "-c",
            "copy",
            "-bsf:a",
            "aac_adtstoasc",
            output_file,
        ]
        
        result = subprocess.run(
            ffmpeg_cmd_protocol, capture_output=True, text=True, timeout=1800
        )
        
        if (
            result.returncode == 0
            and os.path.exists(output_file)
            and os.path.getsize(output_file) > 1024
        ):
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            print(f"Successfully created {output_file} using concat protocol")
            print(f"Output video size: {size_mb:.1f} MB")
            return True
        else:
            print(f"Strategy 2 failed: {result.stderr[:300] if result.stderr else 'Unknown error'}...")
    except subprocess.TimeoutExpired:
        print("Strategy 2 timed out")
    except Exception as e:
        print(f"Strategy 2 error: {e}")

    # Strategy 3: Binary concatenation then remux
    print("\nStrategy 3: Trying binary concatenation then remux...")
    try:
        temp_concat_file = os.path.join(input_dir, "temp_concat.ts")
        
        # Concatenate all .ts files into one
        print("Concatenating TS files...")
        with open(temp_concat_file, "wb") as outfile:
            for i, ts_file in enumerate(ts_files):
                if i % 100 == 0:
                    print(f"Processing file {i}/{len(ts_files)}...")
                with open(ts_file, "rb") as infile:
                    outfile.write(infile.read())
        
        print("Remuxing concatenated file to MP4...")
        ffmpeg_cmd_remux = [
            "ffmpeg",
            "-y",
            "-i",
            temp_concat_file,
            "-c",
            "copy",
            "-bsf:a",
            "aac_adtstoasc",
            output_file,
        ]
        
        result = subprocess.run(
            ffmpeg_cmd_remux, capture_output=True, text=True, timeout=1800
        )
        
        # Clean up temp file
        try:
            os.remove(temp_concat_file)
        except Exception:
            pass
        
        if (
            result.returncode == 0
            and os.path.exists(output_file)
            and os.path.getsize(output_file) > 1024
        ):
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            print(f"Successfully created {output_file} using binary concatenation")
            print(f"Output video size: {size_mb:.1f} MB")
            return True
        else:
            print(f"Strategy 3 failed: {result.stderr[:300] if result.stderr else 'Unknown error'}...")
    except subprocess.TimeoutExpired:
        print("Strategy 3 timed out")
    except Exception as e:
        print(f"Strategy 3 error: {e}")

    # Strategy 4: Re-encode with error recovery
    print("\nStrategy 4: Trying re-encoding with error recovery...")
    try:
        concat_file = os.path.join(input_dir, "segment_list.txt")
        with open(concat_file, "w") as f:
            for ts_file in ts_files:
                abs_path = os.path.abspath(ts_file)
                f.write(f"file '{abs_path}'\n")
        
        ffmpeg_cmd_reencode = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file,
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-max_muxing_queue_size",
            "9999",
            "-err_detect",
            "ignore_err",
            "-fflags",
            "+genpts+igndts",
            output_file,
        ]
        
        print("Running ffmpeg re-encoding (this will take longer)...")
        result = subprocess.run(
            ffmpeg_cmd_reencode, capture_output=True, text=True, timeout=3600
        )
        
        try:
            os.remove(concat_file)
        except Exception:
            pass
        
        if (
            result.returncode == 0
            and os.path.exists(output_file)
            and os.path.getsize(output_file) > 1024
        ):
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            print(f"Successfully created {output_file} using re-encoding")
            print(f"Output video size: {size_mb:.1f} MB")
            return True
        else:
            print(f"Strategy 4 failed: {result.stderr[:300] if result.stderr else 'Unknown error'}...")
    except subprocess.TimeoutExpired:
        print("Strategy 4 timed out")
    except Exception as e:
        print(f"Strategy 4 error: {e}")

    # Strategy 5: Force with maximum error tolerance
    print("\nStrategy 5: Trying maximum error tolerance approach...")
    try:
        # First, try to analyze one segment to get stream info
        probe_cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-print_format",
            "json",
            ts_files[0]
        ]
        
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
        
        concat_file = os.path.join(input_dir, "segment_list.txt")
        with open(concat_file, "w") as f:
            for ts_file in ts_files:
                abs_path = os.path.abspath(ts_file)
                f.write(f"file '{abs_path}'\n")
        
        ffmpeg_cmd_force = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file,
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-bsf:a",
            "aac_adtstoasc",
            "-avoid_negative_ts",
            "make_zero",
            "-fflags",
            "+discardcorrupt+genpts+igndts",
            "-err_detect",
            "ignore_err",
            "-ignore_unknown",
            "-max_muxing_queue_size",
            "9999",
            "-f",
            "mp4",
            output_file,
        ]
        
        result = subprocess.run(
            ffmpeg_cmd_force, capture_output=True, text=True, timeout=1800
        )
        
        try:
            os.remove(concat_file)
        except Exception:
            pass
        
        if os.path.exists(output_file) and os.path.getsize(output_file) > 1024:
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            print(f"Created {output_file} with error recovery")
            print(f"Output video size: {size_mb:.1f} MB")
            if result.returncode != 0:
                print("Warning: Video created with errors, but should be playable")
            return True
    except Exception as e:
        print(f"Strategy 5 error: {e}")

    # Strategy 6: Try treating files as raw H264/H265 streams
    print("\nStrategy 6: Trying to treat as raw video streams...")
    try:
        # Try to concatenate and convert as raw stream
        temp_concat_file = os.path.join(input_dir, "temp_raw.h264")
        
        print("Concatenating as raw stream...")
        with open(temp_concat_file, "wb") as outfile:
            for i, ts_file in enumerate(ts_files[:100]):  # Test with first 100 files
                if i % 20 == 0:
                    print(f"Processing file {i}/100...")
                with open(ts_file, "rb") as infile:
                    outfile.write(infile.read())
        
        # Try to convert raw stream to MP4
        ffmpeg_cmd_raw = [
            "ffmpeg",
            "-y",
            "-f",
            "h264",
            "-i",
            temp_concat_file,
            "-c:v",
            "copy",
            "-f",
            "mp4",
            os.path.join(input_dir, "test_raw.mp4"),
        ]
        
        result = subprocess.run(
            ffmpeg_cmd_raw, capture_output=True, text=True, timeout=60
        )
        
        try:
            os.remove(temp_concat_file)
        except Exception:
            pass
        
        if result.returncode != 0:
            # Try as HEVC/H265
            print("H264 failed, trying as H265/HEVC...")
            temp_concat_file = os.path.join(input_dir, "temp_raw.h265")
            with open(temp_concat_file, "wb") as outfile:
                for i, ts_file in enumerate(ts_files[:100]):
                    with open(ts_file, "rb") as infile:
                        outfile.write(infile.read())
            
            ffmpeg_cmd_raw = [
                "ffmpeg",
                "-y",
                "-f",
                "hevc",
                "-i",
                temp_concat_file,
                "-c:v",
                "copy",
                "-f",
                "mp4",
                os.path.join(input_dir, "test_raw.mp4"),
            ]
            
            result = subprocess.run(
                ffmpeg_cmd_raw, capture_output=True, text=True, timeout=60
            )
            
            try:
                os.remove(temp_concat_file)
            except Exception:
                pass
    except Exception as e:
        print(f"Strategy 6 error: {e}")

    print("\nAll MPEGTS combination strategies failed.")
    print("\n⚠️  IMPORTANT: The files appear to be encrypted or in an unsupported format.")
    print("\nPossible solutions:")
    print("1. Check if the M3U8 file contains #EXT-X-KEY tag (encryption info)")
    print("2. Use a tool that supports HLS decryption (e.g., youtube-dl, yt-dlp)")
    print("3. The segments might need special headers or decryption keys")
    print("4. Try using a different downloader that handles encryption")
    print("\nThe downloaded segment files are available in:", input_dir)
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Download video segments from M3U8 file and combine to MP4"
    )
    parser.add_argument("m3u8_file_path", help="Path to the M3U8 file")
    parser.add_argument(
        "--output-dir",
        "-o",
        default="downloaded_segments",
        help="Output directory for downloaded files (default: downloaded_segments)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Optional base URL for relative segment paths in M3U8 file",
    )
    parser.add_argument(
        "--no-combine-video",
        action="store_true",
        help="Skip combining segment files into MP4 video",
    )
    parser.add_argument(
        "--video-output",
        "-v",
        default="output_video.mp4",
        help="Output MP4 filename (default: output_video.mp4)",
    )
    args = parser.parse_args()
    m3u8_file_path = args.m3u8_file_path
    output_directory = args.output_dir
    base_url = args.base_url
    download_success = download_segments_from_m3u8(
        m3u8_file_path, output_directory, base_url
    )
    if not download_success:
        sys.exit(1)
    if not args.no_combine_video:
        print("Combining all segment files into MP4...")
        combine_success = combine_mpegts_segments_to_mp4(
            output_directory, args.video_output
        )
        if not combine_success:
            print("Video combination failed, but downloaded files are available.")
            sys.exit(1)


if __name__ == "__main__":
    main()
