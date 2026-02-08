class iHeater:

    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')

        self.chamber_target = config.getfloat('chamber_target') # target chamber temperature, °C
        self.start_offset = config.getfloat('start_offset') # printing can start when chamber reaches (target - offset)
        self.delta_temp = config.getfloat('delta_temp') # delta between chamber and heater, °C
        self.min_heater_temp = config.getfloat('min_heater_temp') # minimum heater temperature (for cooling), °C
        self.max_heater_temp = config.getfloat('max_heater_temp') # maximum heater temperature, °C
        self.control_interval = config.getint('control_interval') # interval for control function call, seconds
        self.air_min_delta = config.getfloat('air_min_delta') # minimum difference between desired and current chamber temperature (heater temp = desired + delta_temp), °C
        self.air_max_delta = config.getfloat('air_max_delta') # maximum difference between desired and current chamber temperature (heater temp = max_heater_temp), °C
        self.verbose_logging = bool(config.getint('verbose_logging', False))
        self.running = False
        self.target_chamber_temp = 0
        self.target_delta_temp = 0
        self.target_max_heater_temp = 0
        self.previous_target_heater_temp = -1
        self.target_start_offset = 0

        self.iheater_name = self.config.get('iheater_name')
        self.iheater_fan_name = self.config.get('iheater_fan_name')
        self.iheater_chamber_sensor_name = self.config.get('iheater_chamber_sensor_name')

        # Event handlers
        self.printer.register_event_handler('klippy:connect', self.handle_connect)
        self.printer.register_event_handler("klippy:disconnect", self.handle_disconnect)
        self.printer.register_event_handler("klippy:ready", self.handle_ready)

        # iHeater commands
        self.gcode.register_command('IHEATER_OFF', self.cmd_IHEATER_OFF, desc=self.cmd_IHEATER_OFF_help)
        self.gcode.register_command('IHEATER_ON', self.cmd_IHEATER_ON, desc=self.cmd_IHEATER_ON_help)

    def handle_connect(self):
        self.log("Connected", True)

    def handle_disconnect(self):
        self.log("Disconnected", True)

    def handle_ready(self):
        self.log("Ready", True)

        self.iheater = self.get_iheater();
        self.iheater_fan = self.get_iheater_fan();
        self.iheater_chamber_sensor = self.get_iheater_chamber_sensor();

        # Make sure iHeater is off at startup
        self.gcode.run_script_from_command('SET_HEATER_TEMPERATURE HEATER=%s TARGET=1' % self.iheater_name)
        self.gcode.run_script_from_command('SET_HEATER_TEMPERATURE HEATER=%s TARGET=0' % self.iheater_name)

    cmd_IHEATER_OFF_help = "Turns off iHeater"
    def cmd_IHEATER_OFF(self, gcmd):
        self.target_chamber_temp = 0
        self.log("Off", True)

    cmd_IHEATER_ON_help = "Alternative to M141 & M191 with alternative parameter names"
    def cmd_IHEATER_ON(self, gcmd):
        chamber_target = gcmd.get_float('CHAMBER_TEMP', self.chamber_target)
        delta_temp = gcmd.get_float('DELTA', self.delta_temp)
        max_heater_temp = gcmd.get_float('HEATER_MAX', self.max_heater_temp)
        start_offset = gcmd.get_float('CHAMBER_AWAIT_TEMP', self.start_offset)

        if start_offset > 0:
            self.log("M191 S%.1f D%.1f H%.1f W%.1f" % (chamber_target, delta_temp, max_heater_temp, chamber_target - start_offset))
            self.target_start_offset = start_offset
            self.gcode.run_script_from_command('TEMPERATURE_WAIT SENSOR="temperature_sensor %s" MINIMUM=%.1f' % ( self.iheater_chamber_sensor_name, chamber_target - start_offset))
        else:
            self.log("M141 S%.1f D%.1f H%.1f" % (chamber_target, delta_temp, max_heater_temp))
            self.target_chamber_temp = chamber_target
            self.target_delta_temp = delta_temp
            self.target_max_heater_temp = max_heater_temp

        self.previous_target_heater_temp = -1
        
        self.iheater_control()

    def get_iheater(self):
        return self.get_printer_object('heater_generic', self.iheater_name, 'iHeater')

    def get_iheater_fan(self):
        return self.get_printer_object('heater_fan', self.iheater_fan_name, 'iHeater fan')

    def get_iheater_chamber_sensor(self):
        return self.get_printer_object('temperature_sensor', self.iheater_chamber_sensor_name, 'iHeater chamber sensor fan')

    def get_printer_object(self, type, object_name, name):
        printer_object = self.printer.lookup_object('%s %s' % (type, object_name), None)

        if printer_object is None:
            raise self.config.error("%s with name '%s' not found" % (name, object_name))
        
        return printer_object;

    def get_iheater_chamber_temp(self):
        return self.iheater_chamber_sensor.get_temp(self.reactor.monotonic())[0]

    def get_iheater_temp(self):
        return self.iheater.get_temp(self.reactor.monotonic())[0]

    def iheater_control(self):
        self.reactor.register_timer(self._iheater_control, self.reactor.monotonic())

    def _iheater_control(self, eventtime=None):
        target_heater_temp = 0

        if self.target_chamber_temp > 0:
            # Heating is active
            temp_diff = self.target_chamber_temp - self.get_iheater_chamber_temp()

            # MapToRange
            value = temp_diff
            inputLowerBound = self.air_min_delta
            inputUpperBound = self.air_max_delta
            outputLowerBound = self.target_delta_temp
            outputUpperBound = max(self.target_delta_temp, self.max_heater_temp - self.target_chamber_temp)

            slope = (outputUpperBound - outputLowerBound) / (inputUpperBound - inputLowerBound)
            clamped = sorted([inputLowerBound, value, inputUpperBound])[1]
            adjustment = outputLowerBound + slope * (clamped - inputLowerBound)
            target_heater_temp = round(self.target_chamber_temp + adjustment, 2)

            if target_heater_temp != self.previous_target_heater_temp:
                self.log("Set iHeater to %.1f°C" % target_heater_temp)

            self.previous_target_heater_temp = target_heater_temp
            self.running = True
        elif self.get_iheater_temp() > self.min_heater_temp:
            # Heating is off, but heater hasn't cooled down yet
            self.target_chamber_temp = 0
            self.log("Cooling down %.1f°C..." % self.get_iheater_temp())
        else:
            # Heater cooled down - end macro
            self.log("Cooldown complete %.1f°C." % self.min_heater_temp)
            self.running = False

        self.iheater.set_temp(target_heater_temp)

        if not self.running:
            return self.reactor.NEVER
        else:
            return eventtime + self.control_interval
        
    def log(self, message, always_log=False):

        if always_log or self.verbose_logging:
            self.gcode.respond_info("iHeater: %s" % message)

def load_config(config):
    return iHeater(config)