# Echo Careers 首页动态背景提示词

把参考图左侧首页的曲面当作构图和材质参考。交付无文字、无 UI 的背景。首页点阵标题、上传按钮和导航由 HTML 单独覆盖，中央区域要留出稳定的低对比暗部（浅色版为稳定的暖灰背景）。

> Render a seamless 16-second loop of an abstract, physically lit, continuous 3D terrain for a quiet recruiting homepage. A low camera looks across a broad concave basin, past a nearer asymmetric ridge to a softer ridge in the distance. The nearest ridge enters from the lower left and lifts toward the right; its shallow dip leaves the center open for headings. Use fine matte ceramic or sandblasted graphite, with micrograin tied to the surface and soft shadows. Begin with charcoal, muted olive-gray midtones and a small soft highlight. For the light version, keep the same geometry and camera and use warm chalk, stone-gray shadows and a trace of desaturated olive. Keep the center's luminance stable and the bottom fading into the page background.
>
> Over one loop, deform the front and rear ridges in different phases. Give the camera a slow, small sideways sway with a little vertical travel and let a broad area light move across the slopes. Motion should be readable within two seconds through edge deformation, parallax and changing shadows. Match both the pose and velocity at the loop seam. Keep edges soft and the grain still in surface space. Keep the frame free of text, icons, numbers, glass reflections, illuminated outlines, lens flares, floating particles and rapid zooms.
>
> Compose 16:9 and 9:16 independently. In portrait, keep both ridges inside the crop and increase their separation in depth so the center stays readable without relying on horizontal camera movement. For video export, render a clean 1920 × 1080 master and a separately composed portrait master at 30 frames per second. Judge the result at normal speed on a dark and a light UI, including at 360 px width.

## 网页验收

- 运行画面应能观察到山脊形状、前后层相对位置和受光变化。静止时仍能读清标题。
- 当前站点用实时网格按视口重构构图。移动端固定目标 30 帧，桌面以 60 帧为目标。画布像素预算随设备和帧间隔调整；滚出首屏和切进工作台时暂停。
- 验证减少动态效果和省流量时可正常使用静态背景。未启用 WebGL 的设备使用轻量 CSS 呼吸动效。从页面实际打开文件选择器并进入示例工作台。
