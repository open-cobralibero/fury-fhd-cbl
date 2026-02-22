##############################################################################################################################################
#  Fury-FHD Renderer for Enigma2
#  Coded by Youchie SmartCam Tem (c)2025
#  If you use this Renderer for other skins and rename it, please keep the second line adding your credits below
#
##############################################################################################################################################


from Components.Renderer.Renderer import Renderer
from enigma import ePixmap

class furyCamIconS(Renderer):
    GUI_WIDGET = ePixmap

    def __init__(self):
        Renderer.__init__(self)
        self.cam_icons = {
            "oscam": "/usr/share/enigma2/Fury-FHD/CAM/oscam.png",
            "cccam": "/usr/share/enigma2/Fury-FHD/CAM/cccam.png",
            "ncam": "/usr/share/enigma2/Fury-FHD/CAM/ncam.png",
            "smartcam": "/usr/share/enigma2/Fury-FHD/CAM/smartcam.png",
            "gcam": "/usr/share/enigma2/Fury-FHD/CAM/gcam.png",
            "scam": "/usr/share/enigma2/Fury-FHD/CAM/scam.png",
            "gbox": "/usr/share/enigma2/Fury-FHD/CAM/gbox.png",
            "wicardd": "/usr/share/enigma2/Fury-FHD/CAM/wicardd.png",
            "camd3": "/usr/share/enigma2/Fury-FHD/CAM/camd3.png",
            "mgcamd": "/usr/share/enigma2/Fury-FHD/CAM/mgcamd.png",
        }
        self.default_icon = "/usr/share/enigma2/Fury-FHD/CAM/default.png"

    def changed(self, what):
        if not self.instance:
            return

        cam_name = self.source.text.lower() if self.source and self.source.text else ""

        icon_path = self.default_icon
        for key in self.cam_icons:
            if key in cam_name:
                icon_path = self.cam_icons[key]
                break

        try:
            self.instance.setPixmapFromFile(icon_path)
            self.instance.show()
        except Exception as e:
            print(f"Error loading CAM icon: {e}")
            self.instance.hide()

