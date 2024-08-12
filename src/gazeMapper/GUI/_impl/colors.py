from imgui_bundle import imgui

error       = imgui.ImVec4(*imgui.ImColor.hsv(0.9667,.88,.64))
error_bright= imgui.ImVec4(*imgui.ImColor.hsv(0.9667,.88,.93))
error_dark  = imgui.ImVec4(*imgui.ImColor.hsv(0.9667,.88,.43))

ok          = imgui.ImVec4(0.0000, 0.8500, 0.0000, 1.)