#<!-- Shows connection type: "Ethernet", "WIFI", "Modem", or "Offline" -->
#<widget source="furyRouteInfo" render="Label" position="100,100" size="200,25" font="Regular;18">
#    <convert type="furyRouteInfo">All</convert>
#</widget>

#<!-- Shows green when connected, red when offline -->
#<widget source="furyRouteInfo" render="Pixmap" position="100,130" size="25,25">
#    <convert type="furyRouteInfo">All</convert>
#    <convert type="ConditionalShowHide">True</convert>
#</widget>

from Components.Converter.Converter import Converter
from Components.Element import cached

class furyRouteInfo(Converter, object):
    Info = 0
    Lan = 1
    Wifi = 2
    Modem = 3
    All = 4  # New type for combined checking

    def __init__(self, type):
        Converter.__init__(self, type)
        if type == 'Info':
            self.type = self.Info
        elif type == 'Lan':
            self.type = self.Lan
        elif type == 'Wifi':
            self.type = self.Wifi
        elif type == 'Modem':
            self.type = self.Modem
        elif type == 'All':  # Handle the new type
            self.type = self.All

    @cached
    def getBoolean(self):
        if self.type == self.All:
            # Check all interfaces
            for line in open('/proc/net/route'):
                if (line.split()[0] in ['eth0', 'wlan0', 'ra0', 'ppp0'] and 
                    line.split()[3] == '0003'):
                    return True
            return False
        else:
            # Original logic for specific interfaces
            for line in open('/proc/net/route'):
                if self.type == self.Lan and line.split()[0] == 'eth0' and line.split()[3] == '0003':
                    return True
                elif self.type == self.Wifi and (line.split()[0] == 'wlan0' or line.split()[0] == 'ra0') and line.split()[3] == '0003':
                    return True
                elif self.type == self.Modem and line.split()[0] == 'ppp0' and line.split()[3] == '0003':
                    return True
            return False

    boolean = property(getBoolean)

    @cached
    def getText(self):
        if self.type == self.All:
            # Return the active interface type with proper labels
            for line in open('/proc/net/route'):
                if line.split()[3] == '0003':
                    if line.split()[0] == 'eth0':
                        return 'Ethernet'
                    elif line.split()[0] in ['wlan0', 'ra0']:
                        return 'WIFI'
                    elif line.split()[0] == 'ppp0':
                        return 'Modem'
            return 'Offline'  # No active connection
        else:
            # Original logic but with updated labels
            for line in open('/proc/net/route'):
                if self.type == self.Info and line.split()[0] == 'eth0' and line.split()[3] == '0003':
                    return 'Ethernet'
                elif self.type == self.Info and (line.split()[0] == 'wlan0' or line.split()[0] == 'ra0') and line.split()[3] == '0003':
                    return 'WIFI'
                elif self.type == self.Info and line.split()[0] == 'ppp0' and line.split()[3] == '0003':
                    return 'Modem'
            return ''

    text = property(getText)

    def changed(self, what):
        Converter.changed(self, what)