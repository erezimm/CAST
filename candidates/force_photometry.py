import requests
import json

# page18 https://arxiv.org/pdf/2305.16279

def submit_post(ra_list,dec_list):
    ra = json.dumps(ra_list)
    print(ra)
    dec = json.dumps(dec_list)
    print(dec)
    jds = 2458216.1234 # start JD for all input target positions.
    jdstart = json.dumps(jds)
    print(jdstart)
    jde = 2458450.0253 # end JD for all input target positions.
    jdend = json.dumps(jde)
    print(jdend)
    email = 'joeastronomer@caltech.edu'
    userpass = 'pass'
    payload = {'ra': ra, 'dec': dec,
                'jdstart': jdstart, 'jdend': jdend,
                'email': email, 'userpass': userpass}
    
    # fixed IP address/URL where requests are submitted:
    url = 'https://ztfweb.ipac.caltech.edu/cgi-bin/batchfp.py/submit'
    r = requests.post(url,auth=('ztffps', 'dontgocrazy!'), data=payload)
    print("Status_code=",r.status_code)

# Main calling program. Ensure "List_of_RA_Dec.txt"
# contains your RA Dec positions.
with open('List_of_RA_Dec.txt') as f:
    lines = f.readlines()  
f.close()
print("Number of (ra,dec) pairs =", len(lines))
ralist = []
declist = []
i = 0
for line in lines:
    x = line.split()
    radbl = float(x[0])
    decdbl = float(x[1])
    raval = float('%.7f'%(radbl))
    decval = float('%.7f'%(decdbl))
    ralist.append(raval)
    declist.append(decval)
    i = i + 1
    rem = i % 1500 # Limit submission to 1500 sky positions.
    if rem == 0:
        submit_post(ralist,declist)
    ralist = []
    declist = []
if len(ralist) > 0:
    submit_post(ralist,declist)
exit(0)