# How `watermark_remover.py` works

The script removes semi-transparent text or logo watermarks such as the tiled
"©コピー禁止" overlay on `data/IMG_7675.jpg`. Its main idea:

> A semi-transparent watermark does not destroy the photo underneath, it only
> mixes with it. If we know *how much* it mixed, we can undo the mixing and get
> the real pixels back. Only the pixels we cannot undo are painted over by a
> neural inpainting network.

Undoing the mix restores the true texture (water ripples, skin detail).
Inpainting has to invent texture. That is why the script inpaints as little
as possible.

## Pipeline

```
input image
   │
   ├─ 1. detect      find thin bright (or dark) strokes        → stroke response
   ├─ 2. lattice     find the repeat pattern, stack copies     → clean watermark template
   ├─ 3. background  rough guess of the image without strokes  → B
   ├─ 4. unblend     estimate opacity α per pixel, invert blend → J (recovered image)
   ├─ 5. residual    pixels still showing a stroke              → small mask
   └─ 6. inpaint     LaMa fills only that mask                  → output
```

---

## 0. The blending model

A watermark of colour $c$ with opacity $\alpha \in [0,1)$ is drawn over the
clean image $J$. The observed pixel is a linear mix:

$$
I(p) = \alpha(p)\,c + \bigl(1-\alpha(p)\bigr)\,J(p) \tag{1}
$$

For this kind of overlay, $c$ is white (255) for the text and black (0) for
the faint drop shadow around it. Solving (1) for $J$:

$$
J(p) = \frac{I(p) - \alpha(p)\,c}{1-\alpha(p)} \tag{2}
$$

So the whole problem comes down to three questions:

1. **Where** is the watermark? (the mask)
2. **How opaque** is it at each pixel? ($\alpha$)
3. **Which colour** does each pixel blend towards? (white or black)

Once these are known, (2) gives the clean pixel directly.

Equation (2) divides by $1-\alpha$. As $\alpha \to 1$ the watermark is almost
opaque, and any noise in $I$ gets amplified enormously. The script therefore
applies (2) only up to `unblend.alpha_max` (default 0.6) and sends anything
more opaque to inpainting (step 6).

---

## 1. Detect thin strokes: top-hat filter

Watermark text is made of **thin** lines that are brighter than their
surroundings. A morphological **top-hat** finds exactly that. The script works
on the lightness channel $L$ of Lab:

$$
\text{tophat}(L) = L - \text{open}(L), \qquad \text{open} = \text{dilate}(\text{erode}(L)) \tag{3}
$$

An opening with a disk wider than the stroke (`detect.stroke_kernel`, 7 px)
erases anything thinner than the disk. Subtracting the opening, as in (3),
keeps *only* those thin bright things. Large bright regions, such as the white
cap, are kept by the opening and so cancel out. For dark watermarks the mirror
operation (black-hat) is used.

The top-hat responds to **all** thin bright structures: the watermark, but
also water highlights and wet-skin sparkles. Steps 2–4 separate them.

---

## 2. Find the repeat pattern and build a clean template

### 2a. The lattice from autocorrelation

The watermark repeats on a regular 2-D grid (a *lattice*). Every copy sits at
$p + i\,\mathbf v_1 + j\,\mathbf v_2$ for integers $i, j$. To find
$\mathbf v_1, \mathbf v_2$, the script computes the **autocorrelation** of the
stroke response $r$, which measures how similar the image is to itself when
shifted by $\mathbf v$:

$$
A(\mathbf v) = \sum_p r(p)\, r(p+\mathbf v) \tag{4}
$$

For a repeating pattern, (4) has strong peaks exactly at the lattice vectors.
The script computes it with the FFT ($A = \mathcal F^{-1}\{|\mathcal F r|^2\}$)
and then:

- picks the strongest peaks farther than `lattice.min_period` px from the
  origin (closer peaks come from the texture inside a single letter);
- tries pairs of non-parallel peaks as $(\mathbf v_1, \mathbf v_2)$ and keeps
  the pair whose combinations $\mathbf v_1+\mathbf v_2$, $\mathbf v_1-\mathbf v_2$,
  $2\mathbf v_1$, … *also* have peaks, which is what a real lattice requires;
- refines the two vectors to sub-pixel accuracy with a least-squares fit over
  all the lattice peaks it can see.

On the sample this finds $\mathbf v_1 \approx (12.4, -123.6)$ and
$\mathbf v_2 \approx (183.3, 41.2)$ px, with score 0.18. The score must
exceed `lattice.min_score` (0.12).

### 2b. Median stacking

The key trick: shift the image by every lattice vector
$\mathbf s_k = i\,\mathbf v_1 + j\,\mathbf v_2$ (about 40 copies) and take the
**per-pixel median**:

$$
T(p) = \operatorname*{median}_k \; h\bigl(p + \mathbf s_k\bigr) \tag{5}
$$

Here $h$ is a high-pass of $L$ (lightness minus its local median blur).

Why (5) works:

- At a watermark pixel, **every** shifted copy also lands on the same letter
  stroke, so all ~40 values agree and the median keeps the stroke.
- The **photo** under each copy is different (water in one, skin in another,
  float in a third). An image detail appears in only one or two copies, and
  the median rejects it as an outlier.

$T$ is therefore a clean picture of the watermark alone, with no photo
content. The mask is simply $|T|$ above a robust threshold:

$$
\text{thr} = \operatorname{median}(|T|) + k \cdot 1.4826 \cdot \operatorname{MAD}(|T|) \tag{6}
$$

MAD is the median absolute deviation. $1.4826 \cdot \text{MAD}$ estimates the
standard deviation without being dragged up by the strokes themselves.

The sign of $T$ splits the mask into two layers:

| layer     | $T$ | meaning                   | blend colour $c$ |
|-----------|-----|---------------------------|------------------|
| primary   | > 0 | the white letters         | white (255)      |
| secondary | < 0 | the faint dark drop shadow | black (0)        |

---

## 3. Background guess $B$

To measure $\alpha$ we need a rough idea of what is *under* the watermark.
The script uses OpenCV's Telea inpainting (a fast, classical method that
propagates surrounding colours inwards) on the slightly dilated mask. The
result $B$ is blurry but has the right **local brightness and colour**, which
is all step 4 needs.

---

## 4. Estimate opacity $\alpha$ and unblend

Replacing the unknown $J$ by the guess $B$ in (1) and rearranging:

$$
I - B \approx \alpha\,(c - B) \tag{7}
$$

This is a straight line through the origin with slope $\alpha$. With one
pixel there are 3 equations (R, G, B) for one unknown. With the lattice there
are also ~40 copies that share the **same** $\alpha$, because opacity belongs
to the watermark, not to the photo. Least squares over channels and copies
gives, from (7):

$$
\alpha(p) = \frac{\sum_{k}\sum_{\text{ch}} \bigl(I_k - B_k\bigr)\bigl(c - B_k\bigr)}
                 {\sum_{k}\sum_{\text{ch}} \bigl(c - B_k\bigr)^2},
\qquad I_k = I(p+\mathbf s_k),\; B_k = B(p+\mathbf s_k) \tag{8}
$$

A nice property of (8) is that each copy is **weighted by its contrast**
$(c - B_k)^2$:

- A white letter on dark water has high contrast, so $\alpha$ is easy to
  read there and that copy counts a lot.
- A white letter on a white float has $c - B_k \approx 0$, so $\alpha$ is
  invisible there and that copy barely counts.

The copies on easy backgrounds thereby tell us $\alpha$ for the copies on
hard backgrounds.

With $\alpha$ from (8) and $c$ from the layer table, (2) recovers $J$.

**Why not fit the colour $c$ too?** Equation (1) could also be fitted for
$c$, but the regression is biased: $B$ is noisy, and noise in the input
variable pulls the fitted slope towards zero (the *errors-in-variables*
effect). That produces a wrong grey $c$ and channel-dependent $\alpha$, which
shows up as coloured ghosts. Fixing $c$ to pure white/black gives the same
$\alpha$ in all three channels on the sample. This is a sign that the model
is right: the overlay behaves like a "screen" toward white plus a "multiply"
toward black.

---

## 5. Residual mask

After unblending, the script runs the top-hat (3) again on $J$. Any primary
pixel that still has a strong stroke response (`residual.threshold_k`), plus
any pixel with $\alpha >$ `alpha_max`, goes into a small **residual mask**,
dilated by 1 px. On the sample this is about 4.7% of the image, versus 13.8%
for the full watermark mask.

The threshold is a trade-off:
- **Too low:** large areas are sent to the network, which fills them with
  plausible but blotchy texture.
- **Too high:** faint ghosts of the letters remain.

---

## 6. Inpaint the residual with LaMa

[LaMa](https://github.com/advimman/lama) is a CNN inpainting network built on
**Fast Fourier Convolutions**. Some of its layers operate in the frequency
domain, so even early layers see the whole image. That makes it good at
continuing large-scale texture such as water ripples. The script uses an ONNX
export (`Carve/LaMa-ONNX`, fixed 512×512 input) run with onnxruntime on CPU.

Images larger than 512 px are processed in overlapping 512×512 tiles:

1. Only tiles that contain residual pixels are run.
2. Overlapping outputs are averaged with weights that fall off towards tile
   edges, so no seams appear.
3. The result is pasted back **only** inside the residual mask, with a
   feathered edge.

Everywhere else the output is the unblended $J$ from step 4.

---

## When there is no repeat pattern

If no lattice reaches `lattice.min_score`, step 2 cannot separate watermark
from content, because nothing tells a white letter from a white highlight.
The script then becomes conservative:

- It assumes a light watermark (logged as `polarity=light (assumed)`). A dark
  assumption would treat pupils, hair and shadows as watermark.
- The mask is the top-hat response (3) with a strict threshold (6)
  (`detect.threshold_k` = 10), restricted to low-saturation pixels, since
  watermarks are near-grey.
- There is no shadow layer, and $\alpha$ comes from (7) using only the three
  colour channels of a single pixel.

This mode works noticeably worse than the tiled one. Expect some real
highlights to be dimmed and some watermark to remain.

---

## Reading the log line

```
data/IMG_7675.jpg -> result/IMG_7675.jpg  polarity=light lattice=(12.4,-123.6)/(183.3,41.2) score=0.18 copies=41 mask=13.8% inpainted=4.7%
```

| field       | meaning                                                                       |
|-------------|-------------------------------------------------------------------------------|
| `polarity`  | light (white) or dark watermark; `(assumed)` when chosen without a lattice    |
| `lattice`   | the two repeat vectors $(dy,dx)$ in px, or `none`                             |
| `score`     | autocorrelation strength of the lattice (higher = more certain)               |
| `copies`    | how many shifted copies were stacked in (5) and (8)                           |
| `mask`      | share of pixels treated as watermark (unblended)                              |
| `inpainted` | share of pixels handed to LaMa; lower means more real texture was recovered   |

## Tuning tips

- **Letters left behind on a high-resolution image:** raise
  `detect.stroke_kernel`. The disk in (3) must be wider than a stroke.
- **Lattice not found** (`lattice=none` on an obviously tiled watermark):
  lower `lattice.min_score` slightly.
- **Faint ghosts remain:** lower `residual.threshold_k` to send more to LaMa.
- **Blotchy patches:** raise `residual.threshold_k` to send less to LaMa.
