import requests
import json
import uuid

from datetime import datetime

from .database import Database, DBHandle
from .theExceptions import CreationError, ConnectionError
from .users import Users

class JsonHook(object) :
    """This one replaces requests' original json() function. If a call to json() fails, it will print a message with the request content"""
    def __init__(self, ret) :
        self.ret = ret
        self.ret.json_originalFct = self.ret.json
    
    def __call__(self, *args, **kwargs) :
        try :
            return self.ret.json_originalFct(*args, **kwargs)
        except Exception as e :
            print( "Unable to get json for request: %s. Content: %s" % (self.ret.url, self.ret.content) )
            raise e 

class AikidoSession(object) :
    """Magical Aikido being that you probably do not need to access directly that deflects every http request to requests in the most graceful way.
    It will also save basic stats on requests in it's attribute '.log'.
    """

    class Holder(object) :
        def __init__(self, fct, auth, verify=True) :
            self.fct = fct
            self.auth = auth
            if verify != None:
              self.verify = verify 

        def __call__(self, *args, **kwargs) :
            if self.auth :
                kwargs["auth"] = self.auth
            if self.verify != True:
                kwargs["verify"] = self.verify

            try :
                ret = self.fct(*args, **kwargs)
            except :
                print ("===\nUnable to establish connection, perhaps arango is not running.\n===")
                raise

            if len(ret.content) < 1:
                raise ConnectionError("Empty server response", ret.url, ret.status_code, ret.content)
            elif ret.status_code == 401 :
                raise ConnectionError("Unauthorized access, you must supply a (username, password) with the correct credentials", ret.url, ret.status_code, ret.content)

            ret.json = JsonHook(ret)
            return ret

    def __init__(self, username, password, verify=True) :
        if username :
            self.auth = (username, password)
        else :
            self.auth = None

        self.verify = verify 
        self.session = requests.Session()
        self.log = {}
        self.log["nb_request"] = 0
        self.log["requests"] = {}

    def __getattr__(self, k) :
        try :
            reqFct = getattr(object.__getattribute__(self, "session"), k)
        except :
            raise AttributeError("Attribute '%s' not found (no Aikido move available)" % k)

        holdClass = object.__getattribute__(self, "Holder")
        auth = object.__getattribute__(self, "auth")
        verify = object.__getattribute__(self, "verify")
        log = object.__getattribute__(self, "log")
        log["nb_request"] += 1
        try :
            log["requests"][reqFct.__name__] += 1
        except :
            log["requests"][reqFct.__name__] = 1

        return holdClass(reqFct, auth, verify)

    def disconnect(self) :
        try:
            self.session.close()
        except Exception :
            pass

class Connection(object) :
    """This is the entry point in pyArango and directly handles databases."""
    def __init__(self, arangoURL = 'http://127.0.0.1:8529', username = None, password = None, verify = True, verbose = False, statsdClient = None, reportFileName = None) :
        self.databases = {}
        self.verbose = verbose
        if arangoURL[-1] == "/" :
            if ('url' not in vars()):
                raise Exception("you either need to define `url` or make arangoURL contain an HTTP-Host")
            self.arangoURL = url[:-1]
        else :
            self.arangoURL = arangoURL

        self.identifier = None
        self.startTime = None
        self.session = None
        self.resetSession(username, password, verify)

        self.URL = '%s/_api' % self.arangoURL
        if not self.session.auth :
            self.databasesURL = '%s/database/user' % self.URL
        else :
            self.databasesURL = '%s/user/%s/database' % (self.URL, username)

        self.users = Users(self)

        if reportFileName != None:
            self.reportFile = open(reportFileName, 'a')
        else:
            self.reportFile = None

        self.statsdc = statsdClient
        self.reload()

    def disconnectSession(self) :
        if self.session: 
            self.session.disconnect()

    def resetSession(self, username=None, password=None, verify=True) :
        """resets the session"""
        self.disconnectSession()
        self.session = AikidoSession(username, password, verify)
        
    def reload(self) :
        """Reloads the database list.
        Because loading a database triggers the loading of all collections and graphs within,
        only handles are loaded when this function is called. The full databases are loaded on demand when accessed
        """

        r = self.session.get(self.databasesURL)

        data = r.json()
        if r.status_code == 200 and not data["error"] :
            self.databases = {}
            for dbName in data["result"] :
                if dbName not in self.databases :
                    self.databases[dbName] = DBHandle(self, dbName)
        else :
            raise ConnectionError(data["errorMessage"], self.databasesURL, r.status_code, r.content)

    def createDatabase(self, name, **dbArgs) :
        "use dbArgs for arguments other than name. for a full list of arguments please have a look at arangoDB's doc"
        dbArgs['name'] = name
        payload = json.dumps(dbArgs, default=str)
        url = self.URL + "/database"
        r = self.session.post(url, data = payload)
        data = r.json()
        if r.status_code == 201 and not data["error"] :
            db = Database(self, name)
            self.databases[name] = db
            return self.databases[name]
        else :
            raise CreationError(data["errorMessage"], r.content)

    def hasDatabase(self, name) :
        """returns true/false wether the connection has a database by the name of 'name'"""
        return name in self.databases

    def __getitem__(self, dbName) :
        """Collection[dbName] returns a database by the name of 'dbName', raises a KeyError if not found"""
        try :
            return self.databases[dbName]
        except KeyError :
            self.reload()
            try :
                return self.databases[dbName]
            except KeyError :
                raise KeyError("Can't find any database named : %s" % dbName)

    def reportStart(self, name):
        if self.statsdc != None:
            self.identifier = str(uuid.uuid5(uuid.NAMESPACE_DNS, name))[-6:]
            if self.reportFile != None:
                self.reportFile.write("[%s]: %s\n" % (self.identifier, name))
                self.reportFile.flush()
            self.startTime = datetime.now()

    def reportItem(self):
        if self.statsdc != None:
            diff = datetime.now() - self.startTime
            microsecs = (diff.total_seconds() * (1000 ** 2) ) + diff.microseconds
            self.statsdc.timing("pyArango_" + self.identifier, int(microsecs))
