// Running on a worker is deliberate: Chromium's main-thread canvas PNG encoder
// uses idle tasks, which can delay every frame while the app is in the background.
let canvas, context;
self.onmessage = async ({ data: { pixels, width, height } }) => {
  try {
    if (!canvas || canvas.width !== width || canvas.height !== height) {
      canvas = new OffscreenCanvas(width, height);
      context = canvas.getContext('2d', { willReadFrequently: true });
    }
    context.putImageData(new ImageData(new Uint8ClampedArray(pixels), width, height), 0, 0);
    const blob = await canvas.convertToBlob({ type: 'image/png' });
    self.postMessage({ blob, pixels }, [pixels]);
  } catch (error) {
    self.postMessage({ error: error.message, pixels }, [pixels]);
  }
};
